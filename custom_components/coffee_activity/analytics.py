"""Bounded diagnostics and operation grouping without invented measurements."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from statistics import median
import math
from .review import manual_recipe

FEATURE_DEFAULTS = {"stale_after_s": 120.0, "max_gap_s": 60.0,
                    "session_gap_s": 360.0, "standalone_milk": False,
                    "energy_entity": "", "energy_enabled": True,
                    "cappuccino_enabled": True, "cappuccino_milk_phases": 2,
                    "cappuccino_cups": 2, "cappuccino_match_mode": "exact"}

def feature_options(raw=None):
    values = {**FEATURE_DEFAULTS, **(raw or {})}
    result = {}
    for key in ("stale_after_s", "max_gap_s", "session_gap_s"):
        value = values[key]
        if isinstance(value, bool):
            raise ValueError("Invalid duration")
        value = float(value)
        if not math.isfinite(value) or not 1 <= value <= 3600:
            raise ValueError("Duration must be between 1 and 3600 seconds")
        result[key] = value
    if result["max_gap_s"] > result["stale_after_s"]:
        raise ValueError("Maximum gap must not exceed stale timeout")
    for key in ("standalone_milk", "energy_enabled", "cappuccino_enabled"):
        if not isinstance(values[key], bool):
            raise ValueError("Expected boolean")
        result[key] = values[key]
    for key in ("cappuccino_milk_phases", "cappuccino_cups"):
        value = values[key]
        if isinstance(value, bool):
            raise ValueError("Recipe quantities must be whole numbers")
        value = float(value)
        if not math.isfinite(value) or not value.is_integer() or not 1 <= value <= 10:
            raise ValueError("Recipe quantities must be integers between 1 and 10")
        result[key] = int(value)
    source = values["energy_entity"]
    if not isinstance(source, str) or (source and not source.startswith("sensor.")):
        raise ValueError("Invalid energy sensor")
    result["energy_entity"] = source
    mode = values["cappuccino_match_mode"]
    if mode not in ("exact", "at_least"):
        raise ValueError("Unknown cappuccino matching mode")
    result["cappuccino_match_mode"] = mode
    return result

class MeasurementQuality:
    def __init__(self):
        self.last_report = None
        self.valid = False
        self.intervals = deque(maxlen=60)
        self.aborted_phases = 0
        self.interrupted = False

    def report(self, timestamp, valid):
        if self.last_report is not None and timestamp <= self.last_report:
            return False
        if self.valid and valid and self.last_report is not None:
            self.intervals.append(timestamp-self.last_report)
        self.last_report = timestamp
        self.valid = valid
        self.interrupted = False
        return True

    def describe(self, now, settings, minimum_duration):
        age = max(0, now-self.last_report) if self.last_report is not None else None
        interval = median(self.intervals) if self.intervals else None
        state = ("missing" if not self.valid else "stale" if self.interrupted or age > settings["stale_after_s"]
                 else "sparse" if interval is not None and interval > minimum_duration/2
                 else "fresh")
        return {"state": state, "age_s": round(age, 1) if age is not None else None,
                "typical_interval_s": round(interval, 1) if interval is not None else None,
                "interval_count": len(self.intervals), "aborted_phases": self.aborted_phases,
                "note": "HA report intervals; source hardware sampling rate is not proven."}

def evidence(event):
    if event.get("corrections"):
        return "corrected"
    if event.get("measurement_quality") in ("sparse", "missing", "stale"):
        return "unclear"
    if event["classification"] == "espresso":
        return "matching"
    if event["classification"] == "milk":
        return "estimated"
    return "unclear"

def sessions(events, gap_s):
    """Derive retained groups afresh so corrections never leave stale summaries."""
    result = []
    confirmed = {}
    for event in events:
        if event["classification"] not in ("espresso", "milk"):
            continue
        try:
            start = datetime.fromisoformat(event["started_at"])
            end = datetime.fromisoformat(event["ended_at"])
            if start.utcoffset() is None or end.utcoffset() is None or end < start:
                continue
            started = start.timestamp()
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        # Each espresso starts a new group. Following milk belongs to that shot.
        annotation = manual_recipe(event.get('manual_recipe'))
        manual = annotation is not None and event['id'] in annotation['event_ids']
        previous = confirmed.get(annotation['id']) if manual else result[-1] if result else None
        join = (event["classification"] == "milk" and previous is not None
                and not manual and not previous.get('manual_recipe')
                and 0 <= started-datetime.fromisoformat(previous["ended_at"]).timestamp() <= gap_s
                and event.get("profile") == previous["profile"])
        if (manual and previous is None) or (not manual and not join):
            previous = {"id": event["id"], "started_at": event["started_at"],
                        "ended_at": event["ended_at"], "profile": event.get("profile"),
                        "espresso": 0, "milk": 0, "event_ids": [], "energy_wh": None,
                        "energy_complete": True}
            result.append(previous)
            if manual:
                previous['manual_recipe'] = annotation
                confirmed[annotation['id']] = previous
        previous[event["classification"]] += 1
        previous["event_ids"].append(event["id"])
        previous["ended_at"] = event["ended_at"]
        energy = event.get("energy_wh")
        if energy is None or event.get("energy_quality") != "measured":
            previous["energy_complete"] = False
        else:
            previous["energy_wh"] = round((previous["energy_wh"] or 0)+energy, 3)
    for group in result:
        group["summary"] = f"{group['espresso']} Espresso + {group['milk']} Milchphasen"
        if not group["energy_complete"]:
            group["energy_wh"] = None
    return sorted(result, key=lambda group: group['started_at'])

def cappuccino_groups(groups, settings, now, *, active_start=None, active_profile=None):
    """Assign cups using an explicit recipe; never infer milk/espresso volume.

    Exact milk count is required. The latest group remains provisional until its
    joining window has elapsed or a subsequent group/profile closes it. A running
    potentially joining phase defers completion even beyond the idle deadline.
    """
    result = []
    for index, group in enumerate(groups):
        ended = datetime.fromisoformat(group["ended_at"])
        if ended > now:
            continue
        latest = index == len(groups)-1
        gap = settings["session_gap_s"]
        can_join = (latest and active_start is not None
                    and (active_profile is None or active_profile == group["profile"])
                    and 0 <= (active_start-ended).total_seconds() <= gap)
        profile_closed = active_profile is not None and active_profile != group["profile"]
        final = not latest or profile_closed or ((now-ended).total_seconds() >= gap and not can_join)
        required = settings["cappuccino_milk_phases"]
        milk_matches = (group["milk"] >= required if settings.get("cappuccino_match_mode") == "at_least"
                        else group["milk"] == required)
        matched = settings["cappuccino_enabled"] and group["espresso"] == 1 and milk_matches
        cups = settings["cappuccino_cups"] if matched else 0
        confirmed = group.get('manual_recipe')
        if confirmed:
            matched = (group['espresso'] == 1 and group['milk'] >= 1
                       and set(group['event_ids']) == set(confirmed['event_ids']))
            cups = confirmed['cups'] if matched else 0
            final = True
        result.append({**group, "cappuccino": cups, "recipe_matched": bool(matched),
                       "recipe_status": "final" if final else "provisional",
                       "recipe_wait_s": max(0, round(gap-(now-ended).total_seconds())) if not final else 0,
                       "waiting_for_phase": bool(can_join),
                       "drink_label": (f"{cups} Cappuccino (bestätigt)" if confirmed else f"{cups} Cappuccino nach Rezept") if matched else None})
    return result

class CappuccinoLedger:
    """Keep recipe totals across journal expiry, with reversible retained groups.

    Keep the activity events of partly retained/open groups so expiry cannot turn
    an espresso-and-milk group into an orphan milk group. Once a closed group's
    last event leaves the journal, freeze its cups in the archived subtotal.
    """
    def __init__(self):
        self.archived = 0
        self.through = 0
        self.events = []
        self.current = 0
        self.since = None
        self.archived_by_day = {}

    @property
    def total(self):
        return self.archived + self.current

    def export(self):
        return {"archived": self.archived, "through": self.through,
                "events": deepcopy(self.events), "current": self.current,
                "since": self.since, "archived_by_day": dict(self.archived_by_day)}

    def restore(self, raw):
        if not isinstance(raw, dict):
            return
        for key in ("archived", "through", "current"):
            value = raw.get(key, 0)
            setattr(self, key, value if type(value) is int and value >= 0 else 0)
        self.since = raw.get("since") if isinstance(raw.get("since"), str) else None
        self.archived_by_day = {}
        days = raw.get("archived_by_day", {})
        for day, cups in days.items() if isinstance(days, dict) else []:
            try:
                datetime.strptime(day, "%Y-%m-%d")
            except (TypeError, ValueError):
                continue
            if type(cups) is int and cups >= 0:
                self.archived_by_day[day] = cups
        self.events = []
        seen = set()
        for event in raw.get("events", []) if isinstance(raw.get("events"), list) else []:
            if (isinstance(event, dict) and type(event.get("id")) is int
                    and event["id"] > self.through and event["id"] not in seen
                    and event.get("classification") in ("espresso", "milk")
                    and sessions([event], 1)):
                self.events.append(deepcopy(event))
                seen.add(event["id"])

    def sync(self, events, settings, now, *, active_start=None, active_profile=None, zone=timezone.utc):
        before = self.export()
        visible = {event["id"] for event in events}
        combined = {event["id"]: event for event in self.events}
        for event in events:
            ident = event["id"]
            if ident <= self.through:
                continue
            if event["classification"] in ("espresso", "milk"):
                combined[ident] = {key: deepcopy(event.get(key)) for key in
                                  ("id", "classification", "started_at", "ended_at", "profile", "manual_recipe")}
            else:
                # A manual correction replaces the previous accounting event.
                combined.pop(ident, None)
        ordered = sorted(combined.values(), key=lambda event: event["id"])
        groups = cappuccino_groups(sessions(ordered, settings["session_gap_s"]), settings, now,
                                  active_start=active_start, active_profile=active_profile)
        if self.since is None and ordered:
            self.since = ordered[0]["started_at"]
        self.current = 0
        for group in groups:
            if (group["recipe_status"] == "final"
                    and not visible.intersection(group["event_ids"])):
                self.archived += group["cappuccino"]
                day = datetime.fromisoformat(group["started_at"]).astimezone(zone).date().isoformat()
                self.archived_by_day[day] = self.archived_by_day.get(day, 0) + group["cappuccino"]
                self.through = max(self.through, max(group["event_ids"]))
            elif group["recipe_status"] == "final":
                self.current += group["cappuccino"]
            # Recipes allow at most ten milk phases. An expired prefix with
            # eleven milk events can no longer be repaired through the journal.
            # Freeze that nonmatching prefix at zero instead of retaining an
            # indefinitely growing group in the accounting store.
            missing = [combined[ident] for ident in group["event_ids"] if ident not in visible]
            if (settings.get("cappuccino_match_mode") != "at_least" and group["id"] not in visible
                    and sum(event["classification"] == "milk" for event in missing) > 10):
                self.through = max(self.through, max(event["id"] for event in missing))
        self.events = [event for event in ordered if event["id"] > self.through]
        minimum = (now.astimezone(zone).date()-timedelta(days=30)).isoformat()
        self.archived_by_day = {day: cups for day, cups in self.archived_by_day.items() if day >= minimum}
        return self.export() != before

    def daily_counts(self, settings, now, zone, *, active_start=None, active_profile=None):
        """Daily counts include frozen history and partly expired accounting groups."""
        result = dict(self.archived_by_day)
        groups = cappuccino_groups(sessions(self.events, settings["session_gap_s"]), settings, now,
                                  active_start=active_start, active_profile=active_profile)
        for group in groups:
            if group["recipe_status"] == "final":
                day = datetime.fromisoformat(group["started_at"]).astimezone(zone).date().isoformat()
                result[day] = result.get(day, 0) + group["cappuccino"]
        return result

class EnergyTracker:
    """Counter differences only, with fresh boundaries and reset detection."""
    def __init__(self):
        self.value = None
        self.at = None
        self.generation = 0

    def report(self, value, timestamp):
        if self.at is not None and timestamp <= self.at:
            return False
        if value is None or not math.isfinite(value) or value < 0:
            self.value = None
            self.at = timestamp
            self.generation += 1
            return True
        if self.value is not None and value < self.value:
            self.generation += 1
        self.value, self.at = value, timestamp
        return True

    def boundary(self, timestamp, tolerance=15):
        if self.value is None or self.at is None or not 0 <= timestamp-self.at <= tolerance:
            return None
        return (self.value, self.generation, self.at)

    def finish(self, baseline, ended, tolerance=15):
        if baseline is None or self.value is None or self.at is None:
            return None, "missing"
        if self.generation != baseline[1]:
            return None, "reset"
        # A boundary value must be reported at/after the end, not reused from before it.
        if not 0 <= self.at-ended <= tolerance:
            return None, "pending"
        delta = self.value-baseline[0]
        if delta <= 0:
            return None, "resolution_limited"
        return round(delta, 3), "measured"
