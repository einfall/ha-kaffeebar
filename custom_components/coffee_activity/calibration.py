"""Explicitly labelled recordings; recommendations never silently change detection."""
from copy import deepcopy
from datetime import datetime, timezone
import math
from statistics import median

LABELS = ("espresso", "milk", "heating", "rinsing")
MAX_RECORDINGS = 30
MAX_SAMPLES = 1200
MAX_SECONDS = 900

class CalibrationRecorder:
    def __init__(self):
        self.records = []
        self.active = None
        self.message = ""
        self._proposal_key = None
        self._proposal_value = None

    def restore(self, records):
        self._proposal_key = None
        self.active = None
        self.records = []
        for record in records[-MAX_RECORDINGS:] if isinstance(records, list) else []:
            if not isinstance(record, dict) or record.get("label") not in LABELS:
                continue
            samples = record.get("samples")
            if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
                continue
            if not all(isinstance(s, list) and len(s) == 2 and
                       all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in s)
                       for s in samples):
                continue
            if not samples or any(b[0] <= a[0] for a, b in zip(samples, samples[1:])):
                continue
            if samples[-1][0]-samples[0][0] > MAX_SECONDS:
                continue
            try:
                started = datetime.fromtimestamp(samples[0][0], timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError):
                continue
            self.records.append({"label": record["label"], "samples": deepcopy(samples), "started_at": started,
                                 "power_entity": record.get("power_entity")})

    def start(self, label, now, power, off_w):
        if label not in LABELS or self.active is not None:
            raise ValueError("Aufnahme läuft bereits oder Aufnahmetyp ist ungültig.")
        if power is None or power >= off_w:
            raise ValueError("Aufnahme vor dem Vorgang bei ruhender Maschine starten.")
        self.active = {"label": label, "started_at": now.isoformat(), "samples": [[now.timestamp(), power]]}
        self.message = ""

    def sample(self, timestamp, power):
        if self.active is None:
            return
        samples = self.active["samples"]
        if power is None or timestamp - samples[0][0] > MAX_SECONDS or len(samples) >= MAX_SAMPLES:
            self.cancel()
            self.message = "Aufnahme abgebrochen: Messausfall oder Aufnahmegrenze erreicht."
            return
        if timestamp > samples[-1][0]:
            samples.append([timestamp, power])

    def cancel(self):
        self.active = None
        self.message = "Aufnahme abgebrochen."

    @staticmethod
    def features(record, options):
        durations, start = [], None
        samples = record["samples"]
        for timestamp, power in samples:
            if start is None and power >= options.phase_start_w:
                start = timestamp
            elif start is not None and power < options.off_w:
                durations.append(round(timestamp - start, 1))
                start = None
        low = [p for _, p in samples if 20 <= p <= 200]
        return {"durations": durations, "peak_w": max(p for _, p in samples),
                "pump_median_w": median(low) if low else None, "sample_count": len(samples)}

    def stop(self, options):
        if self.active is None:
            raise ValueError("Keine Aufnahme gestartet.")
        features = self.features(self.active, options)
        if self.active["samples"][-1][1] >= options.off_w:
            raise ValueError("Warte auf eine echte Ruhemessung nach dem Vorgang.")
        if features["sample_count"] < 3 or not features["durations"]:
            raise ValueError("Zu wenige Messwerte oder kein vollständig aufgezeichneter Vorgang.")
        if self.active["label"] in ("espresso", "milk") and len(features["durations"]) != 1:
            raise ValueError("Pro Aufnahme genau einen Espresso- oder Milchvorgang aufzeichnen.")
        self.records.append(deepcopy(self.active))
        self.records = self.records[-MAX_RECORDINGS:]
        self._proposal_key = None
        self.active = None
        self.message = "Aufnahme gespeichert."

    def proposal(self, options, power_entity=None):
        key = (options, power_entity, tuple(id(record) for record in self.records))
        if key == self._proposal_key:
            return deepcopy(self._proposal_value)
        def remember(result):
            self._proposal_key, self._proposal_value = key, result
            return deepcopy(result)
        records = [r for r in self.records if power_entity is None or r.get("power_entity") == power_entity]
        features = {label: [self.features(r, options) for r in records if r["label"] == label] for label in LABELS}
        espresso = [f for f in features["espresso"] if len(f["durations"]) == 1 and f["pump_median_w"] is not None]
        counts = {label: len(values) for label, values in features.items()}
        if len(espresso) < 3:
            return remember({"counts": counts, "ready": False, "note": "Mindestens drei vollständige Espresso-Aufnahmen benötigt."})
        values = options.as_dict()
        durations = [f["durations"][0] for f in espresso]
        pumps = [f["pump_median_w"] for f in espresso]
        values.update(espresso_min_s=max(1, math.floor(min(durations)*.8)),
                      espresso_max_s=math.ceil(max(durations)*1.2),
                      espresso_pump_min_w=max(1, min(pumps)*.95),
                      espresso_pump_max_w=max(values["phase_start_w"], max(pumps)*1.05))
        values["espresso_max_s"] = max(values["espresso_max_s"], values["espresso_min_s"]+1)
        milk = [f for f in features["milk"] if len(f["durations"]) == 1]
        note = "Vorschlag aus markierten Aufnahmen; vor Auswahl des neuen Profils prüfen."
        if len(milk) >= 3:
            durations = [f["durations"][0] for f in milk]
            values.update(milk_heating_min_s=max(1, math.floor(min(durations)*.8)),
                          milk_heating_max_s=math.ceil(max(durations)*1.2),
                          milk_heating_min_w=max(1, min(f["peak_w"] for f in milk)*.9))
            values["milk_heating_max_s"] = max(values["milk_heating_max_s"], values["milk_heating_min_s"]+1)
        else:
            note += " Milchgrenzen bleiben unverändert: weniger als drei Milchaufnahmen."
        for reference in features["heating"] + features["rinsing"]:
            if reference["pump_median_w"] is not None and values["espresso_pump_min_w"] <= reference["pump_median_w"] <= values["espresso_pump_max_w"] and any(values["espresso_min_s"] <= duration <= values["espresso_max_s"] for duration in reference["durations"]):
                return remember({"counts": counts, "ready": False, "note": "Aufwärmen/Spülen überlappt den Espresso-Vorschlag. Mehr getrennte Aufnahmen oder manuelle Experten-Grenzen nötig."})
            if reference["peak_w"] >= values["milk_heating_min_w"] and any(values["milk_heating_min_s"] <= duration <= values["milk_heating_max_s"] for duration in reference["durations"]):
                if "überlappt Milchphasen" not in note:
                    note += " Aufwärmen/Spülen überlappt Milchphasen; Milch bleibt eine Schätzung."
        return remember({"counts": counts, "ready": True, "thresholds": values, "note": note})
