# HA Kaffeebar

Kaffee-Erkennung für Home Assistant mit Espresso-, Milchphasen- und Cappuccino-Zählern, Live-Status und anpassbaren Maschinenprofilen. Das Profil **Rocket Appartamento** ist enthalten.

**Voraussetzung:** Home Assistant ab 2026.10 und ein Leistungssensor der Kaffeemaschine in W oder kW.

## Installation

1. In HACS über **⋮ → Benutzerdefinierte Repositories** `https://github.com/einfall/ha-kaffeebar` als Typ **Integration** hinzufügen und herunterladen.
2. Home Assistant neu starten.
3. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen → HA Kaffeebar** den Leistungssensor und das Profil auswählen. Änderungen sind anschließend über **Konfigurieren** möglich.

## Karte

Die Karte wird mit der Integration installiert und automatisch registriert. Keine separate Karteninstallation nötig. Im Dashboard **Karte hinzufügen → Kaffee · Tagesauswertung** wählen oder diese Konfiguration verwenden:

```yaml
type: custom:coffee-review-card
```

Sie zeigt Tageszähler, Cappuccino insgesamt und Phasen mit Uhrzeit, Dauer und Erkennungsgrund. Unter **Cappuccino** siehst du die zugehörigen Phasen und warum eine Zubereitung erkannt wurde, noch wartet oder nicht zum Rezept passt. Kategorien lassen sich per Drag & Drop oder Auswahl ändern; zusammengehörige Phasen können als Cappuccino zugeordnet werden. Änderungen werden mit **Speichern** übernommen. Über **Profil lernen** lassen sich eigene Profile aus bestätigten Beispielen erstellen. Die Karte benötigt ein Home-Assistant-Administratorkonto.

Milch- und Cappuccino-Erkennung sind Schätzungen anhand der Leistung und des einstellbaren Rezepts; Milchvolumen wird nicht gemessen. Zunächst das Profil anhand echter Zubereitungen prüfen und bei Bedarf anpassen.

<img width="1438" height="1085" alt="kaffeebar1" src="https://github.com/user-attachments/assets/3cd9588c-4960-4031-b575-6f9207b197f8" />
<img width="1430" height="1278" alt="kaffeebar2" src="https://github.com/user-attachments/assets/99aca6a8-c6c1-4e0a-85af-d35d686ea292" />
