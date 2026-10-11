"""Shared integration constants."""
from homeassistant.const import Platform
from .engine import DEFAULT_OPTIONS

DOMAIN = "coffee_activity"
REVIEW_VERSION = "0.5.6"
DEFAULT_NAME = "Kaffee"
PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SELECT, Platform.BUTTON)
STORAGE_VERSION = 1
LEGACY_ENTITIES = {
    "zubereitungen": ("sensor.kaffee_zubereitungen", "kaffee_zubereitungen_leistung"),
    "status": ("sensor.kaffee_status", "kaffee_status_leistung"),
    "espresso_heute": ("sensor.kaffee_espresso_heute", "kaffee_espresso_heute_leistung"),
    "espresso_gestern": ("sensor.kaffee_espresso_gestern", "kaffee_espresso_gestern_leistung"),
    "milchschaum_heute": ("sensor.kaffee_milchschaum_heute", "kaffee_milchschaum_heute_leistung"),
    "milchschaum_gestern": ("sensor.kaffee_milchschaum_gestern", "kaffee_milchschaum_gestern_leistung"),
    "is_espresso": ("binary_sensor.kaffee_is_espresso", "kaffee_is_espresso_leistung"),
    "is_milchschaum": ("binary_sensor.kaffee_is_milchschaum", "kaffee_is_milchschaum_leistung"),
}
STATUS_LABELS = {"unknown": "Unbekannt", "off": "Aus", "ready": "Bereit", "heating": "Heizen", "espresso": "Espresso (vermutet)", "milk": "Milch/Dampf (geschätzt)", "unclear": "Bezug unklar"}
ACTIVITY_LABELS = {"espresso": "Espresso", "milk": "Milchschaum", "heating": "Aufwärmen", "rinsing": "Spülen", "unknown": "Unbekannt", "unclear": "Unklarer Bezug"}
REASON_LABELS = {"espresso_marker_and_duration": "Espresso-Pumpenbereich und passende Dauer",
    "milk_heating_with_late_refill_pump": "Dampf-/Heizphase mit kurzer später Nachfüllpumpe (geschätzt)",
    "standalone_milk_pump": "Milch-Pumpenbereich ohne Espresso-Kontext (geschätzt)",
    "milk_pump_after_espresso": "Milch-Pumpenbereich nach Espresso",
    "milk_heating_after_espresso": "Heiz-/Dampfphase nach Espresso (geschätzt)",
    "pump_duration_outside_window": "Pumpenbereich erkannt, Dauer außerhalb des Espresso-Fensters",
    "heater_without_confirmed_milk": "Heizen ohne bestätigten Milch-Kontext",
    "unclassified_power_phase": "Leistungsphase ohne eindeutige Zuordnung"}
NEW_MIGRATED_ENTITIES = {
    "review_version": "sensor.kaffee_auswertung_version",
    "cappuccino_total": "sensor.kaffee_cappuccino_gesamt",
    "cappuccino_today": "sensor.kaffee_cappuccino_heute", "cappuccino_yesterday": "sensor.kaffee_cappuccino_gestern",
    "cappuccino_journal": "sensor.kaffee_cappuccino_protokoll", "cappuccino_pending": "sensor.kaffee_cappuccino_vorlaeufig",
    "cappuccino_recipe": "sensor.kaffee_cappuccino_rezept",
    "quality": "sensor.kaffee_messqualitaet", "report_age": "sensor.kaffee_messalter",
    "report_interval": "sensor.kaffee_meldeintervall", "preparation": "sensor.kaffee_letzte_zubereitung",
    "evidence": "sensor.kaffee_erkennungsqualitaet", "last_energy": "sensor.kaffee_letzte_vorgangsenergie",
    "profile_export": "sensor.kaffee_profilexport", "guide": "sensor.kaffee_kalibrierungsassistent",
    "calibration_guided": "button.kaffee_gefuehrte_kalibrierung",
    "phase_duration": "sensor.kaffee_laufende_dauer", "journal": "sensor.kaffee_ereignisprotokoll",
    "calibration": "sensor.kaffee_kalibrierung", "profile": "select.kaffee_profil",
    "event": "select.kaffee_vorgang", "correction_type": "select.kaffee_korrekturtyp",
    "calibration_type": "select.kaffee_kalibrierungstyp",
    "calibration_start": "button.kaffee_kalibrierung_starten",
    "calibration_stop": "button.kaffee_kalibrierung_speichern",
    "calibration_cancel": "button.kaffee_kalibrierung_abbrechen",
    "calibration_profile": "button.kaffee_kalibriertes_profil_speichern",
    "correct_event": "button.kaffee_korrektur_anwenden",
}
