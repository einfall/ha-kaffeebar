"""Espresso counts, estimated milk phases and current machine status."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.util import dt as dt_util
from copy import deepcopy

from .const import DOMAIN, STATUS_LABELS, ACTIVITY_LABELS, REASON_LABELS, REVIEW_VERSION
from .entity import CoffeeEntity
from .analytics import evidence
from .profiles import export_profile

SENSORS = ("zubereitungen", "status", "espresso_heute", "espresso_gestern", "milchschaum_heute", "milchschaum_gestern", "milchphasen_gesamt", "last_espresso", "last_milk", "phase_duration", "journal", "calibration",
           "quality", "report_age", "report_interval", "preparation", "evidence", "last_energy", "profile_export", "guide",
           "cappuccino_today", "cappuccino_yesterday", "cappuccino_journal", "cappuccino_pending", "cappuccino_recipe", "cappuccino_total", "review_version")

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(CoffeeSensor(coordinator, key) for key in SENSORS)

class CoffeeSensor(CoffeeEntity, SensorEntity):
    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator, key)
        if key in ("zubereitungen", "milchphasen_gesamt"):
            # Manual corrections can decrease totals; do not interpret them as resets.
            self._attr_state_class = SensorStateClass.TOTAL
        if key in ("zubereitungen", "espresso_heute", "espresso_gestern"):
            self._attr_native_unit_of_measurement = "Bezüge"
            self._attr_icon = "mdi:coffee"
        elif key.startswith("cappuccino_"):
            self._attr_icon = "mdi:coffee-outline"
            if key != "cappuccino_recipe":
                self._attr_native_unit_of_measurement = "Tassen"
            if key == "cappuccino_total":
                self._attr_state_class = SensorStateClass.TOTAL
            # Derived retained-journal counts can change with recipes/corrections/expiry.
            # They are intentionally not a lifetime total or long-term statistic.
        elif key in ("milchschaum_heute", "milchschaum_gestern", "milchphasen_gesamt"):
            self._attr_native_unit_of_measurement = "Phasen"
            self._attr_icon = "mdi:cup"
        elif key == "status":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(STATUS_LABELS)
            self._attr_icon = "mdi:coffee-maker"
        elif key in ("last_espresso", "last_milk"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_icon = "mdi:history"
        elif key in ("phase_duration", "report_age", "report_interval"):
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = "s"
            self._attr_icon = "mdi:timer-outline"
        elif key in ("quality", "evidence"):
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["fresh", "sparse", "stale", "missing"] if key == "quality" else ["matching", "estimated", "unclear", "corrected", "none"]
            self._attr_icon = "mdi:check-decagram-outline"
        elif key == "guide":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["espresso", "milk", "heating", "rinsing", "complete"]
            self._attr_icon = "mdi:school-outline"
        elif key == "last_energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = "Wh"
            self._attr_icon = "mdi:lightning-bolt"
        elif key == "calibration":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["idle", "recording", "ready"]
            self._attr_icon = "mdi:tune"
        else:
            self._attr_icon = "mdi:clipboard-text-clock-outline"

    @property
    def native_value(self):
        engine = self.coordinator.engine
        if self.key == "review_version":
            return REVIEW_VERSION
        if self.key.startswith("cappuccino_"):
            settings = self.coordinator.features
            if self.key == "cappuccino_total":
                return engine.cappuccino_ledger.total
            if self.key == "cappuccino_recipe":
                quantifier = "mindestens " if settings["cappuccino_match_mode"] == "at_least" else ""
                return (f"1 Espresso + {quantifier}{settings['cappuccino_milk_phases']} Milchphasen → "
                        f"{settings['cappuccino_cups']} Cappuccino") if settings["cappuccino_enabled"] else "Deaktiviert"
            return self.coordinator.cappuccino_counts[self.key.removeprefix("cappuccino_")]
        if self.key in ("quality", "report_age", "report_interval"):
            quality = self.coordinator.measurement_quality
            return quality[{"quality": "state", "report_age": "age_s", "report_interval": "typical_interval_s"}[self.key]]
        if self.key == "preparation":
            groups = self.coordinator.preparations
            return groups[-1]["summary"] if groups else "Noch keine Zubereitung"
        if self.key == "evidence":
            event = self.coordinator.selected_event
            return evidence(event) if event else "none"
        if self.key == "last_energy":
            return engine.events[-1].get("energy_wh") if engine.events else None
        if self.key == "profile_export":
            return engine.profile_name
        if self.key == "guide":
            guide = self.coordinator.calibration_guide
            return guide["next_type"] or "complete"
        if self.key == "phase_duration":
            return engine.phase_duration
        if self.key == "journal":
            return len(engine.events)
        if self.key == "calibration":
            return "recording" if engine.calibration.active else "ready" if self.coordinator.calibration_proposal["ready"] else "idle"
        values = {"zubereitungen": engine.espresso_total, "status": engine.status,
                  "espresso_heute": engine.espresso_today, "espresso_gestern": engine.espresso_yesterday,
                  "milchschaum_heute": engine.milk_today, "milchschaum_gestern": engine.milk_yesterday,
                  "milchphasen_gesamt": engine.milk_total,
                  "last_espresso": dt_util.parse_datetime(engine.last_espresso) if engine.last_espresso else None,
                  "last_milk": dt_util.parse_datetime(engine.last_milk) if engine.last_milk else None}
        return values[self.key]

    @property
    def extra_state_attributes(self):
        engine = self.coordinator.engine
        if self.key.startswith("cappuccino_"):
            settings = self.coordinator.features
            if self.key == "cappuccino_total":
                ledger = engine.cappuccino_ledger
                return {"archivierte_tassen": ledger.archived, "korrigierbare_tassen": ledger.current,
                        "zaehlung_ab": ledger.since, "scope": "initial_available_journal_and_future",
                        "hinweis": "Dauerhaft gespeichert. Start mit dem verfügbaren Einzelprotokoll; ältere Tageszähler werden nicht erfunden. Vorläufige Gruppen zählen nicht. Korrekturen/Rezeptwechsel ändern offene und noch protokollierte Gruppen; vollständig archivierte Gruppen behalten ihre damalige Tassenanzahl."}
            return {"enabled": settings["cappuccino_enabled"], "espresso_per_group": 1,
                    "milk_phases_required": settings["cappuccino_milk_phases"],
                    "cups_per_match": settings["cappuccino_cups"],
                    "match_mode": settings["cappuccino_match_mode"],
                    "grouping_window_s": settings["session_gap_s"],
                    "counts": self.coordinator.cappuccino_counts,
                    "scope": "retained_journal" if self.key == "cappuccino_journal" else "durable_daily_and_accounting_groups",
                    "hinweis": "Tassen nach deinem Rezept, keine Volumenmessung. Heute/Gestern behalten archivierte Gruppen am Starttag; der Protokollzähler bleibt begrenzt. Rezeptwechsel/Korrekturen berechnen noch protokollierte Gruppen neu, archivierte bleiben erhalten. Vorläufige Gruppen zählen separat."}
        if self.key in ("quality", "report_age", "report_interval"):
            return {**self.coordinator.measurement_quality, **self.coordinator.features,
                    "power_entity": self.coordinator.power_entity}
        if self.key == "preparation":
            return {"zubereitungen": self.coordinator.preparations[-10:],
                    "hinweis": "Gruppen aus dem verfügbaren Protokoll, keine gesicherte Anzahl von Tassen."}
        if self.key == "evidence":
            event = self.coordinator.selected_event
            return {"event_id": event["id"] if event else None,
                    "grund": REASON_LABELS.get(event.get("reason"), "Unbekannt") if event else None,
                    "messqualitaet": event.get("measurement_quality") if event else None}
        if self.key == "last_energy":
            event = engine.events[-1] if engine.events else {}
            return {"event_id": event.get("id"), "quality": event.get("energy_quality", "missing"),
                    "energy_entity": self.coordinator.energy_entity or None,
                    "hinweis": "Zählerdifferenz mit Grenzmessungen bis 15 s Abstand; kann benachbarte Heizenergie enthalten. Kein Volumenmaß."}
        if self.key == "profile_export":
            return {"profile_json": export_profile(self.coordinator.configuration),
                    "hinweis": "Import über Konfigurieren → Profil importieren. Keine persönlichen Aufzeichnungen enthalten."}
        if self.key == "guide":
            return self.coordinator.calibration_guide
        if self.key == "zubereitungen":
            return {"letzter_espresso": engine.last_espresso, "letztes_milchschaeumen": engine.last_milk,
                    "letzte_bezugsdauer_sekunden": engine.last_espresso_duration,
                    "letzte_milchphasendauer_sekunden": engine.last_milk_duration,
                    "letzter_unklarer_bezug": engine.last_unclear, "letzte_unklare_bezugsdauer_sekunden": engine.last_unclear_duration,
                    "tageswerte": {day: dict(counts) for day, counts in engine.daily.items()}, "status": STATUS_LABELS[engine.status],
                    "is_espresso": engine.is_espresso, "is_milchschaum": engine.is_milchschaum,
                    "phasenstart": engine.phase_start.timestamp() if engine.phase_start else 0,
                    "espresso_pumpe": engine.espresso_pump, "espresso_live_pumpe": engine.espresso_live_pump,
                    "milch_pumpe": engine.milk_pump, "phasenspitze_w": engine.phase_peak_w,
                    "milch_erkennung": "geschätzte Heiz-/Dampfphasen; keine gesicherten Milchportionen",
                    "power_entity": self.coordinator.power_entity, "profil": engine.profile_name,
                    "erkennungsgrenzen": engine.options.as_dict(), "milch_ohne_espresso": engine.standalone_milk,
                    "messqualitaet": self.coordinator.measurement_quality, **engine.metadata}
        if self.key == "journal":
            return {"profil": engine.profile_name, "aufbewahrung_tage": 30, "max_vorgaenge": 200,
                    "ereignisse": [{**event, "typ": ACTIVITY_LABELS[event["classification"]],
                                    "erkennungsgrund": REASON_LABELS.get(event.get("reason"), "Unbekannt"),
                                    "evidence": evidence(event)}
                                   for event in reversed(deepcopy(engine.events[-10:]))],
                    "hinweis": "Einzelvorgänge erst seit Aktivierung des Protokolls; ältere Zähler wurden übernommen."}
        if self.key == "calibration":
            recorder = engine.calibration
            active = recorder.active
            proposal = self.coordinator.calibration_proposal
            return {"profil": engine.profile_name, "aufnahmetyp": active["label"] if active else None,
                    "messwerte": len(active["samples"]) if active else 0,
                    "meldung": recorder.message, "vorschlag": proposal,
                    "aufnahmen": [{"label": record["label"], "started_at": record["started_at"],
                                   **recorder.features(record, engine.options)} for record in recorder.records[-5:]]}
        if self.key == "milchphasen_gesamt":
            return {"estimated": True, **engine.metadata}
        return None
