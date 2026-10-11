"""Push-based power monitoring, elapsed-time checks and persistent counters."""
from __future__ import annotations

from datetime import timedelta
from copy import deepcopy
import asyncio
import logging
import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STORAGE_VERSION
from .engine import CoffeeEngine, DetectionOptions
from .profiles import normalise_options, select_profile, add_profile
from .analytics import MeasurementQuality, EnergyTracker, sessions, cappuccino_groups
from .maintenance import apply_request
from .review import checks

_LOGGER = logging.getLogger(__name__)

class CoffeeCoordinator(DataUpdateCoordinator[CoffeeEngine]):
    """Listen to one selected sensor; timers never invent power measurements."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_{entry.entry_id}", config_entry=entry)
        self.entry = entry
        self.power_entity = entry.data["power_entity"]
        self.configuration = normalise_options(entry.options)
        self.engine = CoffeeEngine(DetectionOptions.from_mapping(self.configuration), timezone=dt_util.DEFAULT_TIME_ZONE)
        self.engine.profile_name = self.configuration["profile"]
        self.features = self.configuration["features"]
        self.engine.recipe_settings = self.features
        self.engine.standalone_milk = self.features["standalone_milk"]
        self.quality = MeasurementQuality()
        self.energy = EnergyTracker()
        self.energy_entity = self.features["energy_entity"] or (
            "sensor.kaffeemaschine_switch_0_energy" if entry.data.get("migrate_legacy")
            and self.power_entity == "sensor.kaffeemaschine_switch_0_power" else "")
        if not self.features["energy_enabled"]:
            self.energy_entity = ""
        self._energy_baseline = None
        self._pending_energy = {}
        self.guided = False
        self.selected_event_id = None
        self.calibration_type = "espresso"
        self.correction_type = "heating"
        self.store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}", atomic_writes=True)
        self._unsubscribers: list[Any] = []
        self._closed = False
        self.migration_pending = False
        self.maintenance_request = None
        self.review_lock = asyncio.Lock()

    def review_data(self):
        """Read all retained phases, without bloating Recorder entity attributes."""
        from .const import ACTIVITY_LABELS, REASON_LABELS, STATUS_LABELS
        now = dt_util.utcnow()
        events = []
        for event in reversed(self.engine.events):
            historic = event.get('detection_options')
            try:
                options = DetectionOptions.from_mapping(historic or self.configuration['profiles'].get(event.get('profile'), self.configuration))
            except (ValueError, TypeError):
                options = self.engine.options
                historic = None
            row = deepcopy(event)
            row.update(label=ACTIVITY_LABELS[event['classification']],
                       automatic_label=ACTIVITY_LABELS.get(event.get('automatic_classification'), 'Unbekannt'),
                       reason_label=REASON_LABELS.get(event.get('reason'), 'Kein eindeutiges Muster'),
                       checks=checks(event, options), thresholds=options.as_dict(),
                       threshold_scope='Grenzen beim Vorgang' if historic else 'Heutige Profilgrenzen; historische Grenzen nicht gespeichert')
            events.append(row)
        return {'entry_id':self.entry.entry_id, 'name':self.entry.title, 'power_entity':self.power_entity,
                'profile':self.engine.profile_name, 'profiles':list(self.configuration['profiles']),
                'timezone':str(self.engine.timezone), 'today':now.astimezone(self.engine.timezone).date().isoformat(),
                'busy':self.busy, 'status':STATUS_LABELS[self.engine.status], 'live_duration_s':self.engine.phase_duration,
                'phase_start':self.engine.phase_start.isoformat() if self.engine.phase_start else None,
                'events':events, 'daily':deepcopy(self.engine.daily), 'cappuccino_counts':self.cappuccino_counts,
                'cappuccino_daily':self.engine.cappuccino_ledger.daily_counts(self.features,now,self.engine.timezone,
                     active_start=self.engine.phase_start,active_profile=self.engine.profile_name),
                'cappuccino_total':self.engine.cappuccino_ledger.total, 'groups':self.preparations,
                'features':deepcopy(self.features), 'quality':self.measurement_quality,
                'proposal':self.engine.review.proposal(self.engine.options, self.engine.profile_name,self.power_entity,self.features),
                'retention':{'days':30,'max_events':200,'oldest_start':self.engine.events[0]['started_at'] if self.engine.events else None}}

    async def async_review(self, action, payload):
        """One authenticated, serialized user correction; persist before replying."""
        async with self.review_lock:
            if self._closed or self.busy:
                raise ValueError('Maschine oder Aufnahme läuft. Erst danach korrigieren.')
            engine = self.engine
            now = dt_util.utcnow()
            before = engine.export()
            try:
                if action == 'label':
                    event = next((e for e in engine.events if e['id'] == payload['event_id']), None)
                    if event is None or event['started_at'] != payload['started_at']:
                        raise ValueError('Phase ist nicht mehr vorhanden. Ansicht aktualisieren.')
                    if event['classification'] != payload['expected_classification']:
                        raise ValueError('Phase wurde inzwischen geändert. Ansicht aktualisieren.')
                    engine.correct_event(event['id'], payload['classification'], now)
                    # Older journal phases have no recorded source. Keep their
                    # correction, but do not train this source on guessed data.
                    engine.review.label(event, payload['classification'], event.get('power_entity',''), now)
                    event['reviewed_at'] = now.isoformat()
                elif action == 'recipe':
                    requested = payload['events']
                    events = [next((e for e in engine.events if e['id'] == p['id'] and e['started_at'] == p['started_at']), None) for p in requested]
                    if any(e is None for e in events):
                        raise ValueError('Eine Phase fehlt. Ansicht aktualisieren.')
                    if any(e['classification'] != p['classification'] for e,p in zip(events,requested)):
                        raise ValueError('Eine Kategorie wurde inzwischen geändert. Ansicht aktualisieren.')
                    engine.review.confirm_recipe(events, payload['cups'], self.features['session_gap_s'], self.power_entity, now)
                elif action == 'unrecipe':
                    event = next((e for e in engine.events if e['id'] == payload['event_id'] and e['started_at'] == payload['started_at']), None)
                    annotation = event.get('manual_recipe') if event else None
                    if not annotation:
                        raise ValueError('Keine bestätigte Zubereitung an dieser Phase.')
                    if not set(annotation['event_ids']).issubset({e['id'] for e in engine.events}):
                        raise ValueError('Bestätigung teilweise archiviert; nicht mehr rücknehmbar.')
                    for row in engine.events:
                        if row.get('manual_recipe') == annotation:
                            row.pop('manual_recipe', None)
                    engine.review.recipes = [r for r in engine.review.recipes if (r['id'],r['started_at']) != (annotation['id'],annotation['started_at'])]
                elif action == 'save_profile':
                    proposal = engine.review.proposal(engine.options,engine.profile_name,self.power_entity,self.features)
                    if not proposal['ready'] or proposal != payload['expected_proposal']:
                        raise ValueError('Vorschlag fehlt oder hat sich geändert. Ansicht aktualisieren.')
                    options = add_profile(self.entry.options, payload['name'], proposal['thresholds'], features=proposal['features'])
                    await self.store.async_save(engine.export())
                    self.hass.config_entries.async_update_entry(self.entry, options=options)
                    return {'saved_profile':payload['name'], 'active_profile':engine.profile_name}
                else:
                    raise ValueError('Unbekannte Aktion.')
                engine.sync_cappuccino(now)
                await self.store.async_save(engine.export())
            except Exception:
                # Storage/validation failure must not leave optimistic counters.
                engine.restore(before)
                raise
            self.async_set_updated_data(engine)
            return {'saved':True}

    async def async_initialize(self, legacy_snapshot: dict[str, Any] | None = None) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self.engine.restore(stored)
        elif legacy_snapshot:
            self.engine.migrate_template(legacy_snapshot.get("state"), legacy_snapshot.get("attributes", {}))
            self.migration_pending = True
        now = dt_util.utcnow()
        apply_request(self.engine, self.maintenance_request, self.power_entity, now)
        if (self.configuration["rocket_refinement_version"] == 1
                and self.entry.data.get("migrate_legacy")
                and self.power_entity == "sensor.kaffeemaschine_switch_0_power"):
            self.engine.refine_short_rocket_phases(now)
        if self.energy_entity:
            self._energy_reading(self.hass.states.get(self.energy_entity), now)
        self._measure(self.hass.states.get(self.power_entity), now)
        for event in self.engine.events:
            if event.get("energy_quality") == "pending":
                event["energy_quality"] = "interrupted"
        self.engine.sync_cappuccino(now)
        # Durability precedes removal of legacy entity registry entries.
        expected = self.engine.export()
        await self.store.async_save(expected)
        if stored is None:
            readback = await Store(self.hass, STORAGE_VERSION, f"{DOMAIN}.{self.entry.entry_id}", atomic_writes=True).async_load()
            if readback != expected:
                raise ConfigEntryNotReady("Counter storage readback failed; legacy entities retained")
        self.async_set_updated_data(self.engine)

    @property
    def busy(self):
        return self.engine.phase_start is not None or self.engine.calibration.active is not None

    @property
    def selected_event(self):
        return next((event for event in self.engine.events if event["id"] == self.selected_event_id),
                    self.engine.events[-1] if self.engine.events else None)

    @property
    def calibration_proposal(self):
        return self.engine.calibration.proposal(self.engine.options, self.power_entity)

    @property
    def measurement_quality(self):
        return self.quality.describe(dt_util.utcnow().timestamp(), self.features, self.engine.options.espresso_min_s)

    @property
    def preparations(self):
        groups = sessions(self.engine.events, self.features["session_gap_s"])
        return cappuccino_groups(groups, self.features, dt_util.utcnow(),
                                active_start=self.engine.phase_start, active_profile=self.engine.profile_name)

    @property
    def cappuccino_counts(self):
        now = dt_util.utcnow().astimezone(self.engine.timezone)
        daily = self.engine.cappuccino_ledger.daily_counts(self.features, dt_util.utcnow(), self.engine.timezone,
                    active_start=self.engine.phase_start, active_profile=self.engine.profile_name)
        counts = {"today": daily.get(now.date().isoformat(), 0),
                  "yesterday": daily.get((now-timedelta(days=1)).date().isoformat(), 0),
                  "journal": 0, "pending": 0}
        for group in self.preparations:
            if group["recipe_status"] != "final":
                counts["pending"] += group["cappuccino"]
                continue
            counts["journal"] += group["cappuccino"]
        return counts

    @property
    def calibration_guide(self):
        targets = {"espresso": 3, "milk": 3, "heating": 1, "rinsing": 1}
        counts = {label: sum(record["label"] == label and record.get("power_entity") == self.power_entity
                             for record in self.engine.calibration.records) for label in targets}
        next_label = next((label for label in targets if counts[label] < targets[label]), None)
        return {"active": self.guided, "counts": counts, "targets": targets,
                "next_type": next_label, "complete": next_label is None,
                "instruction": "Aufnahme vor dem Vorgang starten, nach der Ruhemessung speichern."
                               if next_label else "Aufnahmen vollständig. Vorschlag prüfen und Profil als Kopie speichern."}

    async def async_choose_profile(self, name):
        if self.busy:
            raise ValueError("Profil erst nach dem Vorgang oder der Aufnahme wechseln.")
        options = select_profile(self.entry.options, name)
        if options == self.entry.options:
            return
        await self.store.async_save(self.engine.export())
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def async_control(self, action):
        engine = self.engine
        now = dt_util.utcnow()
        if action == "calibration_guided":
            if self.busy:
                raise ValueError("Laufenden Vorgang oder Aufnahme zuerst beenden.")
            label = self.calibration_guide["next_type"]
            if label is None:
                raise ValueError("Alle Referenzaufnahmen vorhanden; Vorschlag prüfen.")
            if self.measurement_quality["state"] in ("missing", "stale"):
                raise ValueError("Frische Leistungsmessung benötigt.")
            engine.calibration.start(label, now, engine.power, engine.options.off_w)
            self.guided = True
            self.calibration_type = label
            engine.calibration.active["power_entity"] = self.power_entity
        elif action == "calibration_start":
            if engine.phase_start is not None:
                raise ValueError("Aufnahme vor dem Vorgang starten.")
            if self.measurement_quality["state"] in ("missing", "stale"):
                raise ValueError("Frische Leistungsmessung benötigt.")
            engine.calibration.start(self.calibration_type, now, engine.power, engine.options.off_w)
            engine.calibration.active["power_entity"] = self.power_entity
        elif action == "calibration_stop":
            engine.calibration.stop(engine.options)
            if self.guided and self.calibration_guide["next_type"]:
                self.calibration_type = self.calibration_guide["next_type"]
        elif action == "calibration_cancel":
            engine.calibration.cancel()
            self.guided = False
        elif action == "correct_event":
            if self.busy or self.selected_event is None:
                raise ValueError("Korrektur erst bei ruhender Maschine und mit ausgewähltem Vorgang.")
            engine.correct_event(self.selected_event["id"], self.correction_type, now)
        elif action == "calibration_profile":
            if self.busy:
                raise ValueError("Profil erst nach der Aufnahme speichern.")
            proposal = self.calibration_proposal
            if not proposal["ready"]:
                raise ValueError(proposal["note"])
            name = f"Kalibriert {now.astimezone(engine.timezone):%d.%m.%Y %H:%M:%S}"
            options = add_profile(self.entry.options, name, proposal["thresholds"])
            await self.store.async_save(engine.export())
            self.hass.config_entries.async_update_entry(self.entry, options=options)
            return
        else:
            raise ValueError("Unbekannte Aktion.")
        await self.store.async_save(engine.export())
        self.async_set_updated_data(engine)

    @staticmethod
    def _power(state: State | None) -> float | None:
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            power = float(state.state)
        except (TypeError, ValueError):
            return None
        unit = state.attributes.get("unit_of_measurement")
        if unit == "kW":
            power *= 1000
        elif unit != "W":
            return None
        return power if math.isfinite(power) and power >= 0 else None

    @callback
    def async_start(self) -> None:
        self._unsubscribers.append(async_track_state_change_event(self.hass, [self.power_entity], self._state_changed))
        if self.energy_entity:
            self._unsubscribers.append(async_track_state_change_event(self.hass, [self.energy_entity], self._energy_changed))
        self._unsubscribers.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=self.engine.options.tick_seconds)))
        # Entity setup/storage can await while the source becomes available.
        # Subscribe first, then sample again so its startup event is not lost.
        self._publish(self._measure(self.hass.states.get(self.power_entity), dt_util.utcnow()))

    @staticmethod
    def _report_time(state, now):
        # last_reported also advances for repeated identical HA reports.
        stamp = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
        return stamp.timestamp() if isinstance(stamp, type(now)) and stamp.utcoffset() is not None else now.timestamp()

    def _abort_measurement(self, now):
        self.quality.interrupted = True
        if self.engine.phase_start is not None:
            self.quality.aborted_phases += 1
        self._energy_baseline = None
        return self.engine.process(None, now, sample=True)

    def _measure(self, state, now):
        stamp = self._report_time(state, now)
        power = self._power(state)
        previous = self.quality.last_report
        if not self.quality.report(stamp, power is not None):
            return False
        if stamp > now.timestamp()+1 or now.timestamp()-stamp > self.features["stale_after_s"]:
            self.quality.valid = False
            return self._abort_measurement(now)
        if previous is not None and stamp-previous > self.features["max_gap_s"] and self.busy:
            self._abort_measurement(now)
            # Advance the fresh idle value after reset; don't count an interrupted phase.
            self.engine.reset_partial()
        was_active = self.engine.phase_start is not None
        ident = self.engine.next_event_id
        changed = self.engine.process(power, now)
        if not was_active and self.engine.phase_start is not None:
            # A new phase makes a late boundary for an older phase ambiguous.
            for pending_id in list(self._pending_energy):
                event = next((e for e in self.engine.events if e["id"] == pending_id), None)
                if event:
                    event["energy_quality"] = "overlap"
            self._pending_energy.clear()
            self._energy_baseline = self.energy.boundary(now.timestamp())
        if self.engine.next_event_id != ident:
            event = self.engine.events[-1]
            event['power_entity'] = self.power_entity
            event["measurement_quality"] = self.measurement_quality["state"]
            event["energy_wh"] = None
            event["energy_quality"] = "pending" if self.energy_entity and self._energy_baseline else "missing"
            if event["energy_quality"] == "pending":
                self._pending_energy[event["id"]] = self._energy_baseline
            self._energy_baseline = None
            self._refresh_energy(now)
        return changed

    def _energy_reading(self, state, now):
        value = None
        try:
            unit = state.attributes.get("unit_of_measurement")
            if unit in ("Wh", "kWh"):
                value = float(state.state)*(1000 if unit == "kWh" else 1)
        except (AttributeError, ValueError, TypeError, OverflowError):
            pass
        return self.energy.report(value, self._report_time(state, now))

    def _refresh_energy(self, now):
        changed = False
        for ident, baseline in list(self._pending_energy.items()):
            event = next((e for e in self.engine.events if e["id"] == ident), None)
            if event is None:
                del self._pending_energy[ident]
                continue
            ended = dt_util.parse_datetime(event["ended_at"]).timestamp()
            value, quality = self.energy.finish(baseline, ended)
            if quality == "pending" and now.timestamp()-ended > 15:
                quality = "missing_boundary"
            if quality != "pending":
                event.update(energy_wh=value, energy_quality=quality)
                del self._pending_energy[ident]
                changed = True
        return changed

    @callback
    def _energy_changed(self, event):
        if not self._closed:
            now = dt_util.utcnow()
            self._energy_reading(event.data.get("new_state"), now)
            self._publish(self._refresh_energy(now))

    @callback
    def _publish(self, changed: bool) -> None:
        changed = self.engine.sync_cappuccino(dt_util.utcnow()) or changed
        if changed:
            self.store.async_delay_save(self.engine.export, 1)
        self.async_set_updated_data(self.engine)

    @callback
    def _state_changed(self, event: Any) -> None:
        if self._closed:
            return
        state = event.data.get("new_state")
        changed = self._measure(state, dt_util.utcnow())
        self._publish(changed)

    @callback
    def _tick(self, now: Any) -> None:
        if not self._closed:
            state = self.hass.states.get(self.power_entity)
            # Poll only genuinely newer last_reported values, not cached power each tick.
            changed = False
            if getattr(state, "last_reported", None) is not None:
                changed = self._measure(state, now)
            quality = self.quality.describe(now.timestamp(), self.features, self.engine.options.espresso_min_s)
            age = quality["age_s"]
            if (quality["state"] in ("missing", "stale") or
                    self.busy and age is not None and age > self.features["max_gap_s"]):
                changed = self._abort_measurement(now) or changed
            changed = self.engine.process(None, now, sample=False) or changed
            if self.energy_entity:
                self._energy_reading(self.hass.states.get(self.energy_entity), now)
            changed = self._refresh_energy(now) or changed
            self._publish(changed)

    async def async_stop(self) -> None:
        self._closed = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        await self.store.async_save(self.engine.export())
