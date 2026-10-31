# WRBU Steuerung · V0.1.3

Home-Assistant-App für saisonale Backup-SOC-Regeln mit eigener Oberfläche,
direkter Modbus-Prüfung und integrierten DWD-Warnungen.

[Repository in Home Assistant hinzufügen](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ffbo1982%2FWRBU-Steuerung)

## Einrichtung

1. **Einstellungen → Apps → App installieren → ⋮ → Repositories** öffnen.
2. `https://github.com/fbo1982/WRBU-Steuerung` hinzufügen.
3. **WRBU Steuerung** installieren, starten und **Weboberfläche öffnen** wählen.
4. **Wechselrichter anlegen**: Name, Geräteprofil, IP, TCP-Port und Modbus-Geräte-ID eingeben.
5. **Kommunikation prüfen**. Nach erfolgreichem Test Kategorien bearbeiten und
   **Speichern und aktivieren** wählen.

**Die WR-Prüfung und das Speichern benötigen weder Wetterstandort noch
DWD-Sensor oder Home-Assistant-Verbindung.** Ohne gültige Wetterdaten pausiert
die automatische SOC-Steuerung; der explizite Kommunikationstest ist möglich.

Unterstützte Systeme: Home Assistant mit Supervisor/App-Store auf amd64 oder
aarch64. Zugriff über Ingress, ohne öffentlichen Web-Port. Die App wird bei der
Installation aus dem Quellcode gebaut. HACS ist nicht erforderlich.

## Wetter & Standort – zentral für alle WR

Der eigene Bereich **Wetter & Standort** ist unabhängig von der Geräteverwaltung.

1. Vollständige Anlagenadresse im Format **Straße Hausnummer, PLZ Ort** eingeben.
2. **Adresse prüfen** wählen.
3. Die gefundene **Gemeinde, den Kreis und das Bundesland** kontrollieren.
   Bei mehreren Treffern die passende Warnregion auswählen.
4. **Wetterstandort speichern** wählen. Die App lädt DWD-Warnungen danach direkt.

Eine zusätzliche Home-Assistant-DWD-Integration und die Auswahl einer
Sensor-Entität sind nicht nötig. Ein Wetterstandort gilt für alle angelegten WR.
Die vorhandenen Geräte müssen nach einer Standortänderung nicht erneut
geprüft oder gespeichert werden.

DWD-Warnungen sind gemeindebezogen. Die App liest den Ortsnamen hinter der PLZ
lokal aus der Adresse und sucht damit das DWD-Warngebiet. **Die vollständige
Adresse, Straße, Hausnummer und PLZ werden nicht an einen Adressdienst oder
den DWD übertragen.** Nur der Ortsname und später die DWD-Warngebietskennungen
werden abgefragt. Es findet keine hausgenaue Geokodierung statt. Die richtige
Gemeinde ist deshalb vor dem Speichern bewusst zu prüfen; eine fehlerhafte
Kombination aus PLZ und Ort wird nicht anhand einer externen Adressdatenbank
korrigiert. Für Stockstadt am Rhein lautet die Region:
**Gemeinde Stockstadt am Rhein · Groß-Gerau · Hessen**.

Die Adresse und Warnregion bleiben lokal in `/data/weather.json` gespeichert.
Die Orts-/Warngebietssuche wird lokal zwischengespeichert. Geräte liegen separat
in `/data/devices.json`. Ein fehlgeschlagenes Speichern ersetzt keine
funktionierende Konfiguration.

Die App fragt den offiziellen DWD-Geoserver alle **fünf Minuten** ab.
Warnbeginn und -ende aus bereits geladenen Meldungen werden zusätzlich zur
passenden Uhrzeit ausgewertet. Das ist keine Push-Verbindung zum DWD.
Unbekannte, fehlerhafte, abgeschnittene oder veraltete Daten sind **keine
Entwarnung**: Die Reserve wird dann nicht automatisch abgesenkt.
Nach einem Neustart werden zuerst frische Warnungen geladen.

Die Wetteranzeige zeigt Warnstufe, Warnregion, Zeitpunkt der letzten
erfolgreichen Abfrage und aktuelle Überschriften. Ohne Standort bleiben
WR-Prüfung und Geräteverwaltung nutzbar. Die App liest von Home Assistant
lediglich die Zeitzone; bei Nichterreichbarkeit gilt zunächst Europe/Berlin
bzw. die zuletzt bekannte Zeitzone.

## Kategorien und Steuerung

Jeder WR hat eigene Kategorien mit Name, exklusiv zugeordneten Monaten, normalem
Backup-SOC und Backup-SOC für DWD-Stufen 2–4. Stufe 0/1 verwendet den normalen Wert.
Vorabinformationen, Testmeldungen und noch nicht begonnene Warnungen lösen
keine Unwetterreserve aus.

Belegte Monate sind in anderen Kategorien nicht auswählbar; zusätzlich verhindert
die Prüfung beim Speichern Überschneidungen. Die Warnreserve darf nicht niedriger
als die normale Reserve sein. Nicht zugeordnete Monate bleiben ungesteuert,
auch bei einer DWD-Warnung. Mindestens eine Kategorie ist erforderlich.

Vorbelegt sind Dezember/Januar 40 %, März/November 30 %, sonst 25 %,
jeweils 60 % bei Warnung. Alle Regeln sind bearbeitbar.

Auswertung beim Start mit gültigen Wetterdaten, nach gespeicherten Änderungen,
bei relevanten Warnungsänderungen und am 1. jedes Monats um 00:05 Uhr
in der Home-Assistant-Zeitzone. Gleiche SOC-Sollwerte erzeugen nach erfolgreicher
Ausführung keine weiteren Modbus-Anfragen. Vor einem erforderlichen Schreiben
wird physisch gelesen; ist der Sollwert schon erreicht, entfällt das Schreiben.

Modbus-Fehler führen zu einem einzelnen Retry-Timer nach 30 Sekunden.
Ein neuer Sollwert ersetzt einen ausstehenden alten Auftrag. Fehlende Wetterdaten
pausieren diesen Auftrag. Ein Lock verhindert parallele Schreibvorgänge.
DWD-Abfragen und die fünfsekündige Aktualisierung der Oberfläche erzeugen
keine regelmäßigen Modbus-Schreibzugriffe.

## Kommunikationsprüfung

Die App erkennt Modell und Seriennummer, liest den Backup-SOC, schreibt
**denselben Wert genau einmal** und liest ihn erneut vom Gerät.
Ergebnis, Schreibbestätigung und Rücklesewert werden angezeigt.
Nur ein erfolgreicher Test erlaubt das Speichern. Der Test gilt zehn Minuten,
ist an die WR-Verbindungsdaten gebunden und wird beim Speichern verbraucht.
Änderungen am Wetterstandort beeinflussen diesen Test nicht.

Die Prüfung zeigt ihren aktuellen Schritt und die verstrichene Zeit direkt im Formular.
Auch ein Fehler beim Verbindungsaufbau oder bei der Geräteerkennung erscheint dort.
Der Speichern-Bereich erklärt zusätzlich, warum die Freigabe noch fehlt.
Ein Hintergrundauftrag verhindert lange offene Browseraufrufe; seine Statusabfragen
lesen nur den App-Speicher und lösen keine weiteren Modbus-Schreibversuche aus.

Ein fehlgeschlagener Einrichtungstest wiederholt das Schreiben nicht automatisch.
Der gleiche Wert bestätigt den Modbus-Ablauf, keine reale Notstromumschaltung.
Bei einem Timeout nach einem tatsächlich ausgeführten Schreiben ist keine
physische Exactly-once-Garantie möglich. Ladeverhalten und verfügbare Reserve
hängen vom WR, seinen Einstellungen und dem tatsächlichen Batterieladezustand ab.

## Geräteprofile

- **Sungrow SH – direkter Zugriff:** hinterlegte SH-RS-/SH-RT-/SH-T-Modellliste,
  mit Modell-/Seriennummernprüfung.
- **Sungrow SH über iHomeManager:** die **IP des iHomeManagers** und einen
  freigeschalteten Weiterleitungsport verwenden. Ein bestätigter Aufbau ist
  **Port 503, Geräte-ID 1**. Port und Geräte-ID bleiben installationsabhängig
  und werden nicht automatisch überschrieben.
  Bei angeschlossenem iHomeManager kann der direkte WR-Zugriff gesperrt sein.
  Das Profil prüft die über den iHomeManager erreichbaren SH-Register genauso
  wie beim direkten Zugriff, einschließlich Modell, Seriennummer und
  Schreib-/Rückleseprüfung.
- Das separate eigene EMS-Protokoll des iHomeManagers ist damit nicht
  implementiert. Der frühere gesperrte native Eintrag wird nicht mehr als
  auswählbares Profil angeboten. Die SH-Weiterleitung ist davon unabhängig.

SH-Profil: Dokumentationsregister 13100, Modbus-Adresse 13099, U16, Prozent,
FC03/FC06. Unbekannte Modelle und geänderte Seriennummern werden vor dem
Schreiben abgewiesen. Kein TLS oder serielles Modbus RTU. Weitere Hersteller
können über zusätzliche Adapter in `profiles.py` und `manager.py` ergänzt werden.

Die App schreibt direkt über Modbus und legt keine eigene HA-number-Entität an.
Eine vorhandene `number.battery_reserved_soc_for_backup` kann über deine
bisherige Modbus-Anbindung weiterhin den Gerätewert anzeigen.
Andere Programme sollten keine gegensätzlichen Reservevorgaben schreiben.

## Update von V0.1.0

App-Store aktualisieren, App auf **V0.1.3** aktualisieren und neu starten.
Anschließend die Weboberfläche neu laden. Vorhandene Geräte und Kategorien
bleiben erhalten. Die frühere DWD-Sensorzuordnung im WR-Eintrag wird ignoriert;
den Wetterstandort einmal zentral einrichten. Die früher bereitgestellte
Custom-Integration darf nicht parallel dieselbe Reserve steuern.

App-Daten werden bei Neustarts/Updates erhalten, können aber bei einer
Deinstallation entfernt werden. Die Daten mit Home Assistant sichern.

## Entwicklung und Prüfungen

```shell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m compileall -q wrbu_steuerung/wrbu
```

Die Tests prüfen Speicherung, Kommunikation ohne Wetter/HA, getrennte Formulare,
Warnstufen, Datenfehler, Retry und Nebenläufigkeit. Modbus- und HA-Verbindungen
werden gegen lokale Testserver getestet. Der Workflow baut und startet zusätzlich
Container für amd64 und aarch64. Ein Test mit deinem realen Wechselrichter
steht noch aus.

## Quellen

- [DWD-Geodienste](https://www.dwd.de/DE/wetter/warnungen_aktuell/objekt_einbindung/einbindung_karten_geodienste.pdf?__blob=publicationFile&v=14)
- [DWD-CAP-Warnstufen und Meldungstypen](https://www.dwd.de/DE/leistungen/opendata/help/warnungen/cap_dwd_profile_de_pdf_2_1_14.pdf?__blob=publicationFile&v=3)
- [Home-Assistant-App-Repositories](https://developers.home-assistant.io/docs/apps/repository/)
- [Sungrow-SH-Protokoll](https://forum.iobroker.net/assets/uploads/files/1771841809177-ti_20251119_communication-protocol-of-residential-hybrid-inverter_v1.1.11_en.pdf)

