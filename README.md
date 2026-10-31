# WRBU Steuerung

Home-Assistant-Custom-Integration für eine ereignisgesteuerte Backup-SOC-Reserve.
Frei benennbare Kategorien ersetzen feste Saisonregeln. Jedes angelegte Gerät
bekommt seine eigenen Kategorien und seinen DWD-Sensor.

## Einrichtung

1. Die offizielle DWD-Integration für deine Region einrichten. Für Stockstadt am
   Rhein die Attribute `region_name`/`region_id` des Sensors prüfen.
2. Den Ordner `custom_components/wrbu_steuerung` nach
   `/config/custom_components/wrbu_steuerung` kopieren und HA neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → **WRBU Steuerung**.
4. Gerätenamen, Geräteprofil, IP, TCP-Port, Modbus-Geräte-ID und offiziellen
   DWD-Sensor **Current warning level** auswählen. Standard-Port 502, ID 1.
5. Beim Fortfahren läuft der Test. Die nächste Seite zeigt Modell, Seriennummer,
   gelesenen Backup-SOC, Schreibversuch, Schreibbestätigung und Rücklesewert.
   Bei einem Fehler ist Speichern gesperrt. Zurück zu den Verbindungsdaten,
   korrigieren und erneut absenden. Kein automatischer Test-Schreib-Retry.
6. Kategorien anpassen, anschließend **Speichern und aktivieren** wählen.

Die drei bisherigen Saisonkategorien sind als bearbeitbare Ausgangswerte
vorbelegt. Die spätere Verwaltung erfolgt über **Konfigurieren** am
Integrationseintrag. Verbindungsdaten ändern: Eintrag entfernen und neu anlegen;
dadurch ist erneut ein erfolgreicher Kommunikationstest erforderlich.

Die Integration läuft in Home Assistant, nicht als externer GitHub-App-Dienst.
Repositoryname: `WRBU-Steuerung`; Anzeigename: **WRBU Steuerung**.
Für einen Wechselrichter ist nur ein Eintrag erlaubt, anhand der Seriennummer
auch über verschiedene IP-Adressen/Profile hinweg. Weitere WR werden als weitere
Integrationseinträge angelegt. Parallele Fremdprogramme werden nicht gesperrt.

## Kategorien

Jede Kategorie enthält:

- eigenen Namen, z. B. „XYZ“;
- einen oder mehrere Monate;
- normalen Backup-SOC in ganzen Prozent;
- Backup-SOC für aktive DWD-Warnstufe 2, 3 oder 4.

Belegte Monate werden beim Hinzufügen anderer Kategorien nicht mehr angeboten.
Beim Bearbeiten bleiben die Monate der bearbeiteten Kategorie auswählbar.
Zusätzlich verhindert die Prüfung beim Speichern Überschneidungen. Namen müssen
eindeutig sein; Warnreserve darf nicht unter der normalen Reserve liegen.

Sind alle Monate vergeben, zuerst eine Kategorie bearbeiten oder löschen,
um Monate freizugeben. Löschen gibt die Monate sofort im Entwurf frei.
Erst **Speichern und aktivieren** übernimmt den Entwurf; Abbrechen verwirft ihn.
Es muss mindestens eine Kategorie bestehen. Nicht zugeordnete Monate bleiben
**ungesteuert**, auch bei DWD-Warnungen. Diese Monate zeigt die Übersicht an.

Bei Stufe 0/1 wird der normale Wert verwendet. Bei mehreren gleichzeitigen
Warnungen zählt die höchste aktuelle Stufe. Vorabwarnungen werden abgelehnt.
Alle Warnarten ab Stufe 2 zählen. `unknown`/`unavailable` ist keine Entwarnung:
Bei fehlenden Daten bleibt der WR-Wert erhalten, auch beim Systemstart.

## Geräteprofile und iHomeManager

| Auswahl | Stand |
| --- | --- |
| Sungrow SH – direkter WR-Zugriff | SH-RS, SH-RT und SH-T aus der hinterlegten Modellliste; Modell/Seriennummer werden gelesen |
| Sungrow SH mit iHomeManager – Zugriff direkt auf WR | Gleiches SH-Profil, weiterhin WR-/WiNet-IP verwenden; nicht iHM-IP |
| Über iHomeManager selbst | Gesperrt, zeigt einen verständlichen Fehler; kein geratenes Backup-SOC-Register |

Das SH-Profil verwendet Dokumentationsregister **13100**, Modbus-Adresse
**13099**, U16, 1 %, Lesen FC03, Schreiben FC06. Die Modellliste stammt aus
Sungrows SH-Protokoll V1.1.11. Unbekannte Geräte werden vor jedem Schreiben
abgewiesen. Der native iHomeManager wird durch dieses SH-Protokoll ausdrücklich
nicht abgedeckt. Seine eigenen Modbus-Ports und Register sind separat zu
betrachten. Ein aktueller Firmwarestand allein belegt keine Kompatibilität.

Für native iHM-Unterstützung fehlt hier eine belegte Backup-SOC-Zuordnung.
Die Auswahl ist daher ausdrücklich noch keine Funktionszusage. Das Vorhandensein
eines iHomeManagers in der Anlage verhindert nicht grundsätzlich einen separaten
WR-Zugriff; dessen tatsächliche Erreichbarkeit muss der Test bestätigen.

Keine TLS-Verbindung und kein Modbus-RTU/seriell in dieser Version. Ein TLS-Port
kann nicht mit diesem reinen TCP-Client verwendet werden. Keine Netzwerksuche
oder Schreibversuche auf anderen Registern.

## Kommunikationstest und Betrieb

Der Einrichtungstest identifiziert den WR und führt danach **genau einen**
Schreibversuch mit dem unmittelbar zuvor gelesenen Backup-SOC durch. Dieser
bewusste Schreibzugriff ist auch bei unverändertem Wert erforderlich. Danach
wird derselbe Registerwert erneut vom Gerät gelesen. Erst bei Bestätigung und
Übereinstimmung wird der erfolgreiche Teststatus zum Speichern zugelassen.
Geräte antworten mitunter mit identischem Wert, obwohl eine interne Funktion
gesperrt ist: Dieser Test bestätigt den Modbus-Ablauf, keine reale Notstromprobe.

Im Betrieb werden Regeln nach HA-Start/Laden, am 1. jedes Monats um 00:05 Uhr
HA-Ortszeit und bei DWD-Änderungen ausgewertet. Änderungen gespeicherter
Kategorien laden den Eintrag neu und aktivieren die neuen Regeln sofort.
Zeitzone für Deutschland: `Europe/Berlin`.

Nur ein neuer SOC-Sollwert löst eine Geräteprüfung aus. Ist der reale Wert
bereits korrekt, wird nicht geschrieben. Wiederholte DWD-Meldungen und gleiche
Sollwerte führen auch zu keinen weiteren Modbus-Leseaufrufen. Anders als in der
vorigen Version kann ein Monatswechsel während einer Warnung einen neuen
Warn-SOC erfordern, weil jede Kategorie ihren eigenen Warnwert hat.

Ein fehlgeschlagener Betriebsauftrag wird nach 30 Sekunden erneut geprüft,
gerechnet ab Fehlerfeststellung. Es gibt genau einen Timer und keine
gleichzeitigen Schreibaufträge. Ein anderer Sollwert ersetzt den alten Auftrag;
fehlende DWD-Daten oder ein Monat ohne Regel brechen den Retry ab. Vor jedem
erneuten Schreiben wird physisch gelesen, um Doppelzugriffe nach einem
mehrdeutigen Timeout möglichst zu vermeiden. Eine physische Exactly-once-
Garantie ist bei Verbindungsabbrüchen nicht möglich. Anhaltende Fehler erzeugen
weitere 30-Sekunden-Retries ausschließlich für den ausstehenden Auftrag.

Der Modbus-Client wartet je Anfrage maximal fünf Sekunden und begrenzt
Antwortgrößen; zwischen Anfragen innerhalb einer Sitzung mindestens 1,05 Sekunden.
Kein periodisches Geräte-Polling. Ein vorhandenes Gerät, das an derselben IP
ersetzt wurde, wird anhand der geänderten Seriennummer vor dem Schreiben erkannt.

## Entitäten und Ergebnisanzeige

- **Battery reserved SOC for backup**: `number`, normalerweise
  `number.battery_reserved_soc_for_backup`; HA kann bei Namenskonflikten einen
  anderen Namen vergeben. Wert entspricht der letzten verifizierten Abfrage,
  nicht einer kontinuierlichen Messung. Attribut `last_verified` zeigt den Stand.
- **Kommunikationsstatus**: Diagnose-Sensor mit aktiver Kategorie, gewünschtem
  SOC, letztem Fehler, letzter Verifikation und gespeichertem Einrichtungstest
  im Attribut `setup_test`.

Die number kann manuell über `number.set_value` gesetzt werden. Dieser explizite
Aufruf nutzt denselben seriellen und verifizierten Schreibpfad. Er wird nicht
sofort durch die automatische Regel zurückgesetzt, sondern erst beim nächsten
Sollwertwechsel oder Neustart. Die Integration steuert keine weiteren Register.

Vor dem Umstieg die alte `sungrow_backup_soc`-Integration entfernen und andere
Automationen abstimmen, die denselben SOC schreiben. Kein automatischer Import
alter Einträge. Einstellungen des iHomeManagers oder der Hersteller-App können
ebenfalls auf die Reserve einwirken; diese Integration regelt dann nicht ständig
dagegen an. DWD fragt laut HA-Dokumentation im 15-Minuten-Takt ab; die Reaktion
erfolgt unmittelbar nach Änderung des HA-Sensors. Die Wirkung des Reservewerts
auf Laden/Entladen ist geräte- und konfigurationsabhängig.

## Erweiterung um weitere Wechselrichter

`profiles.py` trennt die Gerätekommunikation von Kategorien und Steuerung.
Ein neuer Adapter implementiert `BackupAdapter`: `identify`, `probe` und `apply`.
Die Factory `create_adapter` und Auswahl `PROFILE_NAMES` erweitern. `probe` muss
denselben Wert einmal schreiben und physisch verifizieren; `apply` muss bei
bereits erreichtem Wert ohne Schreiben zurückkehren. Hersteller-/Modellangaben
und Entitäts-Metadaten für neue Hersteller ergänzen. Keine frei eingebbaren
Registeradressen in der Benutzeroberfläche.

## Entwicklung und Prüfungen

```shell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m compileall -q custom_components
```

Die Tests prüfen Regeln, UI-Ablauf, Auswahl freier Monate, Speichersperre,
Geräteidentität, Test-Schreibanzahl, Rücklesefehler, Retries und Nebenläufigkeit.
Die Modbus-Tests nutzen echte TCP-Pakete gegen einen lokalen Testserver.
HA-Schnittstellen werden in diesen Unit-Tests durch Testdoubles ersetzt;
es handelt sich nicht um einen vollständigen Test mit laufendem Home Assistant.
Ein realer SH-/iHM-Hardwaretest wurde nicht durchgeführt. Kein HACS-Prüfsiegel.

Der beiliegende GitHub-Workflow führt Tests und Syntaxprüfung aus.

## Quellen

- [Sungrow SH-Protokoll V1.1.11, Herstellerdokument auf externem Host](https://forum.iobroker.net/assets/uploads/files/1771841809177-ti_20251119_communication-protocol-of-residential-hybrid-inverter_v1.1.11_en.pdf)
- [Sungrow iHomeManager-Handbuch](https://info-support.sungrowpower.com/application/pdf/2025/07/05/iHomeManager-UEN-202504.pdf)
- [Offizielle Home-Assistant-DWD-Integration](https://www.home-assistant.io/integrations/dwd_weather_warnings/)

Die Herstellerdokumente werden verlinkt und nicht im Repository mitgeliefert.
