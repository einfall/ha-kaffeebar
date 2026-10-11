"""Power-based coffee activity detection, independent of Home Assistant.

The live flags are provisional. Only a real low-power measurement confirms the
end of a phase. Heating after espresso is an estimate of steam activity, not a
measurement of milk volume or an assured count of portions.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone as dt_timezone, tzinfo
import math
from typing import Any
from .calibration import CalibrationRecorder, MAX_SECONDS
from .analytics import CappuccinoLedger, feature_options
from .review import ReviewHistory, manual_recipe

EVENT_TYPES = ("espresso", "milk", "heating", "rinsing", "unknown", "unclear")
MAX_EVENTS = 200
RETENTION_DAYS = 30


@dataclass(frozen=True, slots=True)
class DetectionOptions:
    """Thresholds expressed in watts and seconds, matching the previous YAML."""

    off_w: float = 10
    phase_start_w: float = 35
    espresso_pump_min_w: float = 40
    espresso_pump_max_w: float = 60
    espresso_min_s: float = 20
    espresso_max_s: float = 45
    milk_pump_min_w: float = 60
    milk_pump_max_w: float = 80
    milk_pump_min_s: float = 5
    milk_pump_max_s: float = 120
    milk_heating_min_s: float = 30
    milk_heating_max_s: float = 120
    milk_heating_min_w: float = 1000
    milk_after_espresso_s: float = 360
    live_pump_min_w: float = 80
    live_pump_max_w: float = 100
    tick_seconds: float = 5

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None = None) -> DetectionOptions:
        """Read known options while allowing other config-entry fields."""
        supplied = values or {}
        converted: dict[str, float] = {}
        for field in fields(cls):
            if field.name not in supplied:
                continue
            value = supplied[field.name]
            if isinstance(value, bool):
                raise ValueError(f"{field.name} must be a positive finite number")
            try:
                converted[field.name] = float(value)
            except (ValueError, TypeError, OverflowError) as err:
                raise ValueError(f"{field.name} must be a positive finite number") from err
        result = cls(**converted)
        result.validate()
        return result

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def validate(self) -> None:
        """Reject settings which cannot form coherent detection windows."""
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field.name} must be a positive finite number")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field.name} must be a positive finite number")
        for low, high in (
            ("off_w", "phase_start_w"),
            ("espresso_pump_min_w", "espresso_pump_max_w"),
            ("espresso_min_s", "espresso_max_s"),
            ("milk_pump_min_w", "milk_pump_max_w"),
            ("milk_pump_min_s", "milk_pump_max_s"),
            ("milk_heating_min_s", "milk_heating_max_s"),
            ("live_pump_min_w", "live_pump_max_w"),
        ):
            if getattr(self, low) >= getattr(self, high):
                raise ValueError(f"{low} must be less than {high}")
        # A marker entirely below the phase-start threshold can never be seen
        # when it is the first reading of a phase.
        for high in ("espresso_pump_max_w", "milk_pump_max_w", "live_pump_max_w"):
            if getattr(self, high) < self.phase_start_w:
                raise ValueError(f"{high} must be at least phase_start_w")


DEFAULT_OPTIONS: dict[str, float] = DetectionOptions().as_dict()


def _count(value: Any) -> int:
    """A malformed restored value must never decrement a valid count."""
    if isinstance(value, bool):
        return 0
    try:
        converted = float(value)
        return int(converted) if math.isfinite(converted) and converted >= 0 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.utcoffset() is not None else None


def _duration(value: Any) -> float | None:
    try:
        converted = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return round(converted, 1) if math.isfinite(converted) and converted >= 0 else None


class CoffeeEngine:
    """Track measurements and bounded, persistent coffee counters.

    Startup or missing measurements discard any partial phase. A real idle
    sample is required before counting again, so restoring during heating does
    not reinterpret the remainder of a preparation as a new espresso.
    """

    def __init__(self, options: DetectionOptions, timezone: tzinfo = dt_timezone.utc) -> None:
        options.validate()
        self.options = options
        self.timezone = timezone
        self.espresso_total = 0
        self.milk_total = 0
        self.daily: dict[str, dict[str, int]] = {}
        self.last_espresso: str | None = None
        self.last_milk: str | None = None
        self.last_espresso_duration: float | None = None
        self.last_milk_duration: float | None = None
        self.last_unclear: str | None = None
        self.last_unclear_duration: float | None = None
        self.metadata: dict[str, str] = {}
        self.profile_name = "Rocket Appartamento"
        self.standalone_milk = False
        self.events: list[dict[str, Any]] = []
        self.next_event_id = 1
        self.journal_anchor: dict[str, Any] = {}
        self.cappuccino_ledger = CappuccinoLedger()
        self.recipe_settings = feature_options()
        self.calibration = CalibrationRecorder()
        self.review = ReviewHistory()
        self._local_date: date | None = None
        self._last_seen: float | None = None
        self._last_sample: float | None = None
        self._armed = False
        self.power: float | None = None
        self.available = False
        self.status = "unknown"
        self.is_espresso = False
        self.is_milchschaum = False
        self._reset_phase()

    def _reset_phase(self) -> None:
        self.phase_start: datetime | None = None
        self.espresso_pump = False
        self.first_espresso_marker_s = None
        self.espresso_live_pump = False
        self.milk_pump = False
        self.phase_peak_w = 0.0

    def reset_partial(self) -> None:
        """Forget transient measurements when changing the source or settings."""
        self._reset_phase()
        self._armed = False
        self._last_seen = None
        self._last_sample = None
        self.power = None
        self.available = False
        self.status = "unknown"
        self.is_espresso = False
        self.is_milchschaum = False

    @property
    def today(self) -> date:
        return self._local_date or datetime.now(self.timezone).date()

    @property
    def yesterday(self) -> date:
        return self.today - timedelta(days=1)

    def counts_for_date(self, day: date | str) -> dict[str, int]:
        key = day.isoformat() if isinstance(day, date) else day
        return dict(self.daily.get(key, {"espresso": 0, "milchphasen": 0}))

    @property
    def today_counts(self) -> dict[str, int]:
        return self.counts_for_date(self.today)

    @property
    def yesterday_counts(self) -> dict[str, int]:
        return self.counts_for_date(self.yesterday)

    @property
    def espresso_today(self) -> int:
        return self.today_counts["espresso"]

    @property
    def espresso_yesterday(self) -> int:
        return self.yesterday_counts["espresso"]

    @property
    def milk_today(self) -> int:
        return self.today_counts["milchphasen"]

    @property
    def milk_yesterday(self) -> int:
        return self.yesterday_counts["milchphasen"]

    def export(self) -> dict[str, Any]:
        """Return JSON-safe durable data; never persist a running phase."""
        return {
            "espresso_total": self.espresso_total,
            "milk_total": self.milk_total,
            "daily": {key: dict(value) for key, value in self.daily.items()},
            "last_espresso": self.last_espresso,
            "last_milk": self.last_milk,
            "last_espresso_duration": self.last_espresso_duration,
            "last_milk_duration": self.last_milk_duration,
            "last_unclear": self.last_unclear,
            "last_unclear_duration": self.last_unclear_duration,
            "last_local_date": self._local_date.isoformat() if self._local_date else None,
            "metadata": dict(self.metadata),
            "events": deepcopy(self.events),
            "next_event_id": self.next_event_id,
            "journal_anchor": dict(self.journal_anchor),
            "cappuccino_ledger": self.cappuccino_ledger.export(),
            "calibrations": deepcopy(self.calibration.records),
            "review": self.review.export(),
        }

    def _durable_signature(self):
        # Raw completed recordings are immutable during measurement processing.
        # Avoid deep-copying them on every timer tick in HA's event loop.
        return (self.espresso_total, self.milk_total, self.last_espresso, self.last_milk,
                self.last_unclear, self.last_espresso_duration, self.last_milk_duration,
                self.last_unclear_duration, self._local_date,
                tuple((day, value["espresso"], value["milchphasen"]) for day, value in sorted(self.daily.items())),
                tuple(event["id"] for event in self.events), self.cappuccino_ledger.total)

    def restore(self, data: Mapping[str, Any]) -> None:
        """Restore confirmed state, discarding partial phases and bad fields."""
        self.espresso_total = _count(data.get("espresso_total"))
        self.milk_total = _count(data.get("milk_total"))
        self.daily = {}
        raw_daily = data.get("daily")
        if isinstance(raw_daily, Mapping):
            for key, value in raw_daily.items():
                if not isinstance(key, str) or not isinstance(value, Mapping):
                    continue
                try:
                    day = date.fromisoformat(key)
                except ValueError:
                    continue
                self.daily[day.isoformat()] = {
                    "espresso": _count(value.get("espresso")),
                    "milchphasen": _count(value.get("milchphasen")),
                }
        # Bound an imported document even before the first clock update.
        self.daily = dict(sorted(self.daily.items())[-(RETENTION_DAYS + 1):])
        for name in ("last_espresso", "last_milk", "last_unclear"):
            setattr(self, name, _timestamp(data.get(name)))
        for name in ("last_espresso_duration", "last_milk_duration", "last_unclear_duration"):
            setattr(self, name, _duration(data.get(name)))
        self._local_date = None
        if isinstance(data.get("last_local_date"), str):
            try:
                self._local_date = date.fromisoformat(data["last_local_date"])
            except ValueError:
                pass
        raw_metadata = data.get("metadata")
        self.metadata = (
            {str(key): value for key, value in raw_metadata.items() if isinstance(value, str)}
            if isinstance(raw_metadata, Mapping) else {}
        )
        self.events = []
        raw_events = data.get("events", [])
        seen = set()
        for event in raw_events[-MAX_EVENTS:] if isinstance(raw_events, list) else []:
            if not isinstance(event, dict) or event.get("classification") not in EVENT_TYPES:
                continue
            ident = _count(event.get("id"))
            ended = _timestamp(event.get("ended_at"))
            if not ident or ident in seen or not ended:
                continue
            try:
                date.fromisoformat(event.get("day", ""))
            except (TypeError, ValueError):
                continue
            restored = {**deepcopy(event), "id": ident, "ended_at": ended}
            if 'manual_recipe' in restored:
                validated = manual_recipe(restored['manual_recipe'])
                if validated and ident in validated['event_ids']:
                    restored['manual_recipe'] = validated
                else:
                    restored.pop('manual_recipe', None)
            self.events.append(restored)
            seen.add(ident)
        self.events.sort(key=lambda event: datetime.fromisoformat(event["ended_at"]).timestamp())
        self.next_event_id = max(_count(data.get("next_event_id")), max(seen, default=0)+1)
        anchor = data.get("journal_anchor", {})
        self.journal_anchor = {key: _timestamp(anchor.get(key)) if key.endswith("at") else _duration(anchor.get(key))
                               for key in ("espresso_at", "milk_at", "espresso_duration", "milk_duration")} if isinstance(anchor, Mapping) and anchor else {}
        if not self.journal_anchor and not self.events:
            self.journal_anchor = {"espresso_at": self.last_espresso, "milk_at": self.last_milk,
                                   "espresso_duration": self.last_espresso_duration, "milk_duration": self.last_milk_duration}
        self.calibration.restore(data.get("calibrations", []))
        self.review.restore(data.get("review"))
        self.cappuccino_ledger.restore(data.get("cappuccino_ledger"))
        self.reset_partial()

    def migrate_template(self, state: Any, attributes: Mapping[str, Any]) -> bool:
        """Import the old template snapshot without inventing historic events."""
        before = self.export()
        raw_daily = attributes.get("tageswerte")
        daily = raw_daily if isinstance(raw_daily, Mapping) else {}
        milk_sum = sum(
            _count(value.get("milchphasen"))
            for value in daily.values() if isinstance(value, Mapping)
        )
        metadata = {
            key: attributes[key]
            for key in ("tageswerte_ab", "tageswerte_initialisiert")
            if isinstance(attributes.get(key), str)
        }
        metadata["migration"] = "template_snapshot"
        metadata["milk_total_scope"] = "imported_retained_days_and_new_phases"
        self.restore({
            "espresso_total": state,
            "milk_total": milk_sum,
            "daily": daily,
            "last_espresso": attributes.get("letzter_espresso"),
            "last_milk": attributes.get("letztes_milchschaeumen"),
            "last_espresso_duration": attributes.get("letzte_bezugsdauer_sekunden"),
            "last_milk_duration": attributes.get("letzte_milchphasendauer_sekunden"),
            "last_unclear": attributes.get("letzter_unklarer_bezug"),
            "last_unclear_duration": attributes.get("letzte_unklare_bezugsdauer_sekunden"),
            "metadata": metadata,
        })
        return self.export() != before

    def _roll_day(self, now: datetime) -> None:
        # Capture completed recipes BEFORE the bounded journal drops their shot.
        self.sync_cappuccino(now)
        self._local_date = now.astimezone(self.timezone).date()
        minimum = (self._local_date - timedelta(days=RETENTION_DAYS)).isoformat()
        maximum = self._local_date.isoformat()
        self.daily = {key: value for key, value in self.daily.items() if minimum <= key <= maximum}
        self.daily.setdefault(maximum, {"espresso": 0, "milchphasen": 0})
        removed = [event for event in self.events if event["day"] < minimum]
        kept = [event for event in self.events if event["day"] >= minimum]
        if len(kept) > MAX_EVENTS:
            removed.extend(kept[:-MAX_EVENTS])
            kept = kept[-MAX_EVENTS:]
        for event in removed:
            if event["classification"] in ("espresso", "milk"):
                kind = event["classification"]
                self.journal_anchor[f"{kind}_at"] = event["ended_at"]
                self.journal_anchor[f"{kind}_duration"] = event.get("duration_s")
        self.events = kept
        self.sync_cappuccino(now)

    def sync_cappuccino(self, now: datetime) -> bool:
        return self.cappuccino_ledger.sync(self.events, self.recipe_settings, now,
                                           active_start=self.phase_start,
                                           active_profile=self.profile_name, zone=self.timezone)

    def _late_refill_pump(self, elapsed: float) -> bool:
        """Estimate a short refill tail after heating, without relabeling long shots."""
        options = self.options
        onset = self.first_espresso_marker_s
        return (self._milk_context() and self.espresso_pump and onset is not None
                and self.phase_peak_w >= options.milk_heating_min_w
                and onset >= max(options.espresso_max_s, options.milk_heating_min_s)
                and 0 <= elapsed-onset <= options.milk_pump_min_s
                and options.milk_heating_min_s <= elapsed <= options.milk_heating_max_s)

    def _milk_context(self) -> bool:
        if self.phase_start is None or self.last_espresso is None:
            return False
        gap = self.phase_start.timestamp() - datetime.fromisoformat(self.last_espresso).timestamp()
        return 0 < gap <= self.options.milk_after_espresso_s

    def _finish_phase(self, now: datetime) -> None:
        if self.phase_start is None:
            return
        elapsed = now.timestamp() - self.phase_start.timestamp()
        options = self.options
        espresso = self.espresso_pump and options.espresso_min_s <= elapsed <= options.espresso_max_s
        context = self._milk_context()
        standalone = self.standalone_milk and not context and not self.espresso_pump and self.milk_pump
        late_refill = self._late_refill_pump(elapsed)
        milk = (context or standalone) and not espresso and (
            (self.milk_pump and options.milk_pump_min_s <= elapsed <= options.milk_pump_max_s)
            or (
                context and (not self.espresso_pump or late_refill)
                and self.phase_peak_w >= options.milk_heating_min_w
                and options.milk_heating_min_s <= elapsed <= options.milk_heating_max_s
            )
        )
        unclear = not milk and self.espresso_pump and options.espresso_max_s < elapsed <= max(
            options.milk_pump_max_s, options.milk_heating_max_s
        )
        if not self.events and not self.journal_anchor:
            self.journal_anchor = {"espresso_at": self.last_espresso, "milk_at": self.last_milk,
                                   "espresso_duration": self.last_espresso_duration, "milk_duration": self.last_milk_duration}
        classification = "espresso" if espresso else "milk" if milk else "unclear" if unclear else "heating" if self.phase_peak_w >= options.milk_heating_min_w else "unknown"
        reason = ("espresso_marker_and_duration" if espresso else
                  "standalone_milk_pump" if milk and standalone else
                  "milk_pump_after_espresso" if milk and self.milk_pump else
                  "milk_heating_with_late_refill_pump" if milk and late_refill else
                  "milk_heating_after_espresso" if milk else "pump_duration_outside_window" if unclear else
                  "heater_without_confirmed_milk" if classification == "heating" else "unclassified_power_phase")
        self.events.append({"id": self.next_event_id, "started_at": self.phase_start.isoformat(),
                            "ended_at": now.isoformat(), "day": self.today.isoformat(),
                            "duration_s": round(elapsed, 1), "peak_w": round(self.phase_peak_w, 1),
                            "classification": classification, "automatic_classification": classification,
                            "reason": reason, "profile": self.profile_name,
                            "detection_options": options.as_dict(),
                            "espresso_marker": self.espresso_pump, "milk_marker": self.milk_pump,
                            "first_espresso_marker_s": self.first_espresso_marker_s,
                            "milk_context": self._milk_context(), "corrections": []})
        self.next_event_id += 1
        if espresso:
            self.espresso_total += 1
            self.daily[self.today.isoformat()]["espresso"] += 1
            self.last_espresso = now.isoformat()
            self.last_espresso_duration = round(elapsed, 1)
        if milk:
            self.milk_total += 1
            self.daily[self.today.isoformat()]["milchphasen"] += 1
            self.last_milk = now.isoformat()
            self.last_milk_duration = round(elapsed, 1)
        if unclear:
            self.last_unclear = now.isoformat()
            self.last_unclear_duration = round(elapsed, 1)
        self._roll_day(now)

    @property
    def phase_duration(self) -> float:
        if self.phase_start is None or self._last_seen is None:
            return 0.0
        return round(max(0, self._last_seen - self.phase_start.timestamp()), 1)

    def correct_event(self, event_id: int, classification: str, now: datetime) -> bool:
        """Apply a reversible correction only to a real retained event."""
        if classification not in EVENT_TYPES:
            raise ValueError("Ungültiger Vorgangstyp.")
        event = next((event for event in self.events if event["id"] == event_id), None)
        if event is None or event["day"] not in self.daily:
            raise ValueError("Vorgang nicht mehr im korrigierbaren Protokoll.")
        old = event["classification"]
        if old == classification:
            return False
        for kind, total_name, daily_key in (("espresso", "espresso_total", "espresso"), ("milk", "milk_total", "milchphasen")):
            delta = int(classification == kind) - int(old == kind)
            if getattr(self, total_name)+delta < 0 or self.daily[event["day"]][daily_key]+delta < 0:
                raise ValueError("Korrektur würde einen negativen Zähler erzeugen.")
        # Changing one member dissolves its retained manual drink, rather than
        # keeping a confirmed cappuccino after removing its espresso/milk.
        recipe = event.get("manual_recipe")
        if recipe:
            for item in self.events:
                if item.get("manual_recipe") == recipe:
                    item.pop("manual_recipe", None)
            self.review.recipes = [r for r in self.review.recipes
                                   if (r['id'], r['started_at']) != (recipe['id'], recipe['started_at'])]
        for kind, total_name, daily_key in (("espresso", "espresso_total", "espresso"), ("milk", "milk_total", "milchphasen")):
            delta = int(classification == kind) - int(old == kind)
            setattr(self, total_name, getattr(self, total_name)+delta)
            self.daily[event["day"]][daily_key] += delta
        event["classification"] = classification
        self.review.labels = [r for r in self.review.labels
                              if (r['id'],r['started_at']) != (event['id'],event['started_at'])]
        event["corrections"] = (event.get("corrections", []) + [{"at": now.isoformat(), "from": old, "to": classification}])[-5:]
        for kind in ("espresso", "milk"):
            matches = [item for item in self.events if item["classification"] == kind]
            latest = matches[-1] if matches else None
            setattr(self, "last_espresso" if kind == "espresso" else "last_milk", latest["ended_at"] if latest else self.journal_anchor.get(f"{kind}_at"))
            setattr(self, "last_espresso_duration" if kind == "espresso" else "last_milk_duration", latest["duration_s"] if latest else self.journal_anchor.get(f"{kind}_duration"))
        if old == "unclear" or classification == "unclear":
            matches = [item for item in self.events if item["classification"] == "unclear"]
            latest = matches[-1] if matches else None
            self.last_unclear = latest["ended_at"] if latest else None
            self.last_unclear_duration = latest["duration_s"] if latest else None
        return True

    def refine_short_rocket_phases(self, now: datetime) -> None:
        """Audit old automatic short pump-only labels; preserve manual decisions."""
        for event in self.events:
            if (event.get("profile") == "Rocket Appartamento"
                    and event.get("classification") == "milk" and not event.get("corrections")
                    and event.get("reason") == "milk_pump_after_espresso"
                    and event.get("milk_marker") and not event.get("espresso_marker")
                    and 0 < event.get("duration_s", 0) < 15
                    and 0 < event.get("peak_w", 0) < self.options.milk_heating_min_w
                    and event.get("day") in self.daily):
                self.correct_event(event["id"], "unclear", now)
                event["corrections"][-1]["source"] = "rocket_short_pump_refinement"
                event["corrections"][-1]["note"] = "Pumpenphase unter 15 s; keine bestätigte Milchportion."

    def _update_live(self, now: datetime) -> None:
        self.is_espresso = False
        self.is_milchschaum = False
        if not self.available or self.power is None:
            self.status = "unknown"
            return
        options = self.options
        elapsed = now.timestamp() - self.phase_start.timestamp() if self.phase_start else 0
        if self.phase_start is not None:
            self.is_espresso = self.espresso_live_pump and 0 <= elapsed <= options.espresso_max_s
            self.is_milchschaum = (
                (self._milk_context() or self.standalone_milk and self.milk_pump)
                and ((not self.espresso_pump and not self.espresso_live_pump) or self._late_refill_pump(elapsed))
                and (
                    (self.milk_pump and elapsed <= options.milk_pump_max_s)
                    or (
                        self._milk_context() and self.phase_peak_w >= options.milk_heating_min_w
                        and options.milk_heating_min_s <= elapsed <= options.milk_heating_max_s
                    )
                )
            )
        if self.power < 0.5:
            self.status = "off"
        elif self.is_espresso:
            self.status = "espresso"
        elif self.is_milchschaum:
            self.status = "milk"
        elif self.phase_start is not None and self.espresso_pump and elapsed > options.espresso_max_s:
            self.status = "unclear"
        elif self.power >= options.phase_start_w:
            self.status = "heating"
        else:
            self.status = "ready"

    def process(self, power: float | None, now: datetime, *, sample: bool = True) -> bool:
        """Process a measured sample or time tick, returning durable-change status.

        Duplicate and out-of-order samples are ignored. Ticks use the last real
        measurement and cannot start or end a phase. ``None``, negative values,
        NaN and infinity invalidate an unfinished phase.
        """
        if now.utcoffset() is None:
            raise ValueError("Measurement timestamps must include a timezone")
        timestamp = now.timestamp()
        if self._last_seen is not None and timestamp < self._last_seen:
            return False
        if sample and self._last_sample is not None and timestamp <= self._last_sample:
            return False
        before = self._durable_signature()
        self._last_seen = timestamp
        self._roll_day(now)
        if self.calibration.active and timestamp-self.calibration.active["samples"][0][0] > MAX_SECONDS:
            self.calibration.cancel()
            self.calibration.message = "Aufnahme nach 15 Minuten abgebrochen."
        if sample:
            self._last_sample = timestamp
            try:
                measured = float(power) if power is not None and not isinstance(power, bool) else None
            except (ValueError, TypeError, OverflowError):
                measured = None
            if measured is None or not math.isfinite(measured) or measured < 0:
                self.calibration.sample(timestamp, None)
                self.power = None
                self.available = False
                self._armed = False
                self._reset_phase()
            else:
                self.calibration.sample(timestamp, measured)
                self.power = measured
                self.available = True
                if measured < self.options.off_w:
                    self._finish_phase(now)
                    self._reset_phase()
                    self._armed = True
                elif self._armed:
                    if self.phase_start is None and measured >= self.options.phase_start_w:
                        self.phase_start = now
                    if self.phase_start is not None:
                        marker = self.options.espresso_pump_min_w <= measured <= self.options.espresso_pump_max_w
                        if marker and self.first_espresso_marker_s is None:
                            self.first_espresso_marker_s = now.timestamp()-self.phase_start.timestamp()
                        self.espresso_pump |= marker
                        self.espresso_live_pump |= self.espresso_pump or self.options.live_pump_min_w < measured <= self.options.live_pump_max_w
                        self.milk_pump |= self.options.milk_pump_min_w < measured <= self.options.milk_pump_max_w
                        self.phase_peak_w = max(self.phase_peak_w, measured)
        self._update_live(now)
        return self._durable_signature() != before
