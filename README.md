# WRBU Steuerung · V0.1.0

**Home-Assistant-App (Add-on)** für saisonale Backup-SOC-Regeln und DWD-Schutz.
Die Installation erfolgt direkt über **Einstellungen → Apps → App installieren**.
Wie bei EVCC to PDF und Car2MQTT ist keine HACS-Installation erforderlich.

[Repository in Home Assistant hinzufügen](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ffbo1982%2FWRBU-Steuerung)

## Einrichtung

1. In Home Assistant **Einstellungen → Apps → App installieren** öffnen.
2. Rechts oben **⋮ → Repositories** öffnen.
3. `https://github.com/fbo1982/WRBU-Steuerung` hinzufügen.
4. Unter **WRBU Steuerung** die gleichnamige App auswählen und installieren.
   Die App wird bei der Installation aus dem Quellcode gebaut.
5. App starten; bei Bedarf **Automatisch starten** und **In der Seitenleiste anzeigen**
   aktivieren. **Weboberfläche öffnen** wählen.
6. Die offizielle DWD-Integration für deine Region muss in Home Assistant
   eingerichtet sein. Für Stockstadt am Rhein die Region des ausgewählten
   Sensors kontrollieren.
7. In der App **Wechselrichter anlegen**, Profil, IP, TCP-Port, Geräte-ID und
   aktuellen DWD-Sensor auswählen. Standard für SH: Port 502, Geräte-ID 1.
8. **Kommunikation prüfen**: Es wird identifiziert, der Backup-SOC gelesen,
   derselbe Wert einmal geschrieben und wieder gelesen. Die App zeigt die
   einzelnen Ergebnisse an. Nur ein erfolgreicher Test erlaubt das Speichern.
9. Kategorien anpassen und **Speichern und aktivieren** wählen.

Falls die App nach dem Hinzufügen noch nicht erscheint, den App-Store über
dessen Menü auf Aktualisierungen prüfen und neu öffnen. Die ursprüngliche
Meldung „not a valid app repository“ wird durch die jetzt vorhandene
`repository.yaml` und die vollständige App-Struktur behoben.

Unterstützte Architekturen: **amd64 und aarch64**. Benötigt Home Assistant mit
Supervisor und App-Store. Kein öffentlicher Web-Port: Zugriff erfolgt über
Home Assistant Ingress. Das Supervisor-Token wird automatisch bereitgestellt;
ein persönliches Token muss nicht eingegeben werden.

## Bedienung und Kategorien

Jeder Wechselrichter hat eigene Kategorien, Namen, Monate, einen normalen
Backup-SOC und einen Backup-SOC bei aktuellen DWD-Warnstufen 2, 3 oder 4.
Stufe 0 oder 1 verwendet wieder den normalen Wert.

Ein Monat kann nur einer Kategorie dieses Geräts gehören. Belegte Monate
sind in anderen Kategorien ausgegraut; beim Bearbeiten bleiben die eigenen
Monate auswählbar. Eine zusätzliche serverseitige Prüfung verhindert doppelte
Zuordnungen. Warnreserve darf die normale Reserve nicht unterschreiten.

Die bisherigen drei Saisonregeln sind vorbelegt und frei bearbeitbar:
Winter 40 %, März/November 30 %, sonst 25 %, jeweils 60 % bei Warnung.
Um eine zusätzliche Kategorie anzulegen, bei Bedarf erst Monate aus einer
bestehenden Kategorie freigeben. Nicht zugeordnete Monate bleiben
**ungesteuert, auch bei DWD-Warnungen**; die Oberfläche weist darauf hin.
Mindestens eine Kategorie ist erforderlich.

Kategorien im Editor werden zunächst nur als Entwurf geändert. Abbrechen
verwirft den Entwurf. Speichern aktiviert die neue Konfiguration sofort.
Auch beim Bearbeiten wird die Kommunikation vor dem Speichern geprüft.
Der erfolgreiche Test gilt zehn Minuten, ist an die Verbindungsdaten gebunden
und kann nur einmal zum Speichern verwendet werden. Ein Speichern nach
geänderten Verbindungsdaten erfordert einen neuen Test.

Geräte und Einrichtungstests liegen dauerhaft in `/data/devices.json` und
bleiben bei Neustart und App-Update erhalten. Beschädigte Konfigurationen
werden nicht stillschweigend überschrieben. App-Deinstallation kann diese
Daten entfernen; die App-Daten daher mit Home Assistant sichern.

## Geräteprofile

- **Sungrow SH – direkter Zugriff:** unterstützt die hinterlegte SH-RS-/SH-RT-/
  SH-T-Modellliste nach erfolgreicher Geräteidentifikation.
- **Sungrow SH mit iHomeManager – direkt auf WR zugreifen:** gleicher Zugriff,
  weiterhin IP des Wechselrichters/WiNet statt der iHomeManager-IP eingeben.
- **Über iHomeManager selbst:** explizit gesperrt. Dafür fehlt eine belegte,
  hier implementierte Backup-SOC-Zuordnung. Aktuelle Firmware allein ist kein
  Kompatibilitätsnachweis.

Das SH-Profil nutzt Dokumentationsregister 13100, Modbus-Adresse 13099,
U16, Prozent, FC03/FC06. Unbekannte Modelle und geänderte Seriennummern werden
vor Schreibzugriffen abgewiesen. TLS und serielles Modbus RTU sind nicht enthalten.
Die App verändert keine anderen Wechselrichterregister.

## Steuerung und Kommunikationsschonung

Die App verarbeitet DWD-Zustandsänderungen über die Home-Assistant-WebSocket-API.
Die erste Auswertung erfolgt beim App-Start, sobald gültige HA-/DWD-Daten
vorliegen. Am 1. jedes Monats um 00:05 Uhr wird in der HA-Zeitzone neu ausgewertet.
Neue gespeicherte Einstellungen werden sofort ausgewertet.

Kein periodisches Modbus-Schreiben oder Modbus-Polling. Gleiche Sollwerte
erzeugen nach erfolgreicher Ausführung keine weiteren Modbus-Anfragen.
Bei einem neuen Sollwert wird zuerst physisch gelesen; ist dieser bereits
erreicht, entfällt das Schreiben. Die sichtbaren WR-Werte stammen von der
letzten verifizierten Kommunikation und werden mit Zeitangabe angezeigt.

Bei Verbindungsfehler oder fehlgeschlagener Rückleseprüfung wartet genau ein
Wiederholungsauftrag 30 Sekunden ab Fehlerfeststellung. Der Versuch prüft
erneut die aktuelle Lage und liest vor einem eventuellen Schreiben erneut.
Ein anderer Sollwert ersetzt den alten Auftrag. Ein Lock verhindert parallele
Schreibvorgänge; Warnungsänderungen während eines laufenden Schreibens gehen
nicht verloren. Bei anhaltendem Fehler folgen weitere gezielte 30-Sekunden-
Versuche. Der Einrichtungstest selbst wiederholt niemals automatisch das
Schreiben.

Unbekannte DWD-Werte, fehlende Sensoren oder verlorene HA-Verbindung sind
keine Entwarnung. Die vorhandene Reserve bleibt erhalten. Bei Wiederherstellung
der HA-Verbindung werden die gültigen Zustände neu übernommen.
Die offizielle DWD-Integration fragt normalerweise alle 15 Minuten ab:
Die App reagiert sofort auf HA-Änderungen, nicht unmittelbar auf DWD-Publikationen.

Die Oberfläche aktualisiert nur die Anzeige alle fünf Sekunden aus dem
App-Speicher. Das löst keine Modbus-Anfragen aus. Erneute HA-Anmeldeversuche
nach Verbindungsabbrüchen erfolgen unabhängig davon.

Der gleiche Wert im Einrichtungstest prüft den Modbus-Schreibablauf und die
Rückmeldung, keine reale Notstromumschaltung. Bei einem Timeout nach einem
tatsächlich ausgeführten Schreibvorgang ist keine physische Exactly-once-
Garantie möglich. Ladeverhalten und tatsächlich verfügbare Reserve hängen vom
WR, seiner Konfiguration und dem tatsächlichen Batterieladezustand ab.

## Vorherige Custom-Integration

Dieses Repository wurde auf den ausdrücklich gewünschten App-Store-Weg umgestellt.
Die frühere HACS-/Custom-Integration ist keine Voraussetzung. Falls sie schon
installiert wurde, vor Aktivierung der App entfernen oder deaktivieren.
Andere Automationen und Programme dürfen nicht gleichzeitig gegensätzliche
Backup-SOC-Vorgaben machen.

Die App schreibt direkt über Modbus. Eine bereits vorhandene
`number.battery_reserved_soc_for_backup` aus deiner bisherigen Modbus-Anbindung
kann den Gerätewert weiterhin anzeigen. Die App selbst registriert keine neue
Home-Assistant-number-Entität; die Verwaltung erfolgt in ihrer Weboberfläche.

## Projektstruktur und Erweiterungen

```text
repository.yaml
VERSION
CHANGELOG.md
wrbu_steuerung/
  config.yaml
  Dockerfile
  requirements.txt
  DOCS.md
  wrbu/
    main.py          Ingress-Webserver und API
    manager.py       Speicherung, Prüfung, Steuerung und Retry
    ha.py            HA-Ereignisse und Monatskalender
    profiles.py      Erweiterbare Wechselrichteradapter
    policy.py        Kategorien und SOC-Regeln
    modbus.py        Modbus-TCP
    static/          Deutsche Verwaltungsoberfläche
tests/
.github/workflows/tests.yml
```

Weitere WR werden als weitere Geräte angelegt. Für andere Hersteller wird ein
neuer Adapter mit `identify`, `probe` und `apply` in `profiles.py` ergänzt und
in `manager.py` registriert. Kategorien und UI bleiben davon unabhängig.
Keine frei geratenen Registeradressen im Benutzerformular.

## Prüfungen

```shell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m compileall -q wrbu_steuerung/wrbu
```

Unit- und HTTP-Tests prüfen echte App-Handler, Speicherprüfung, Retry,
Nebenläufigkeit und Kommunikation gegen lokale Modbus-Testserver.
Der GitHub-Workflow baut außerdem den Container für amd64 und aarch64.
Ein Test auf deinem Home-Assistant-Supervisor und mit deinem realen WR steht
noch aus. Softwaretests ersetzen diesen Hardwaretest nicht.

## Quellen

- [Home-Assistant-App-Repositories](https://developers.home-assistant.io/docs/apps/repository/)
- [Home-Assistant-App-Kommunikation](https://developers.home-assistant.io/docs/apps/communication/)
- [Offizielle DWD-Integration](https://www.home-assistant.io/integrations/dwd_weather_warnings/)
- [Sungrow-SH-Protokoll V1.1.11, Herstellerdokument auf externem Host](https://forum.iobroker.net/assets/uploads/files/1771841809177-ti_20251119_communication-protocol-of-residential-hybrid-inverter_v1.1.11_en.pdf)

