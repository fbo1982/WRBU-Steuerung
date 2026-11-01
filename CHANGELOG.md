# Änderungsprotokoll

## V0.1.5

- Drei Reiter: Startseite mit Zustand und Standort, Settings mit Standort-/WR-Verwaltung und Kategorie mit Auswahl des WR.
- Bearbeitungsentwürfe bleiben beim Wechsel der Reiter erhalten; Warnung beim Verwerfen ungespeicherter Änderungen.
- Optionales Abfrageintervall je WR: 0 = Aus, sonst 1–1440 Minuten. Vorhandene Geräte bleiben ohne Intervall.
- Intervall prüft den tatsächlichen Backup-SOC gegen aktuelle Monats-/DWD-Regeln und schreibt nur Abweichungen.
- Letzte Bestätigung und nächster Intervallabgleich werden auf der Startseite angezeigt.
- Timer werden beim Ändern, Deaktivieren, Löschen und Stoppen sauber beendet; bestehender 30-Sekunden-Retry bleibt erhalten.
- DWD-Ereignisauswahl als Folgeschritt dokumentiert; bestehende Warnstufen bleiben wirksam.

## V0.1.4

- Kategorieänderungen an gespeicherten Geräten können ohne erneuten Kommunikationstest gespeichert werden.
- Nur neue oder geänderte WR-IP, Port, Geräte-ID oder Geräteprofile erfordern weiterhin eine neue Prüfbestätigung.
- Jeder Speichervorgang ermittelt Monat und DWD-Warnlage neu und liest den tatsächlichen Backup-SOC.
- Ein abweichender SOC wird geschrieben und zurückgelesen; bei bereits korrektem Wert kein Schreibzugriff.
- Gespeicherte Einstellungen und der Ergebnisstatus des SOC-Abgleichs werden getrennt angezeigt.
- Bei Modbus-Fehlern bleiben die Regeln gespeichert und ein Retry nach 30 Sekunden wird geplant.
- Hintergrund-Speicherauftrag mit Fortschritt und zusätzlichen Backend-/Frontendtests.


## V0.1.3

- Profil zu „Sungrow SH über iHomeManager“ korrigiert.
- Bei iHomeManager-Weiterleitung ausdrücklich dessen IP statt der WR-IP verwenden.
- Bestätigter Aufbau: freigeschalteter Port 503 und Geräte-ID 1; Werte bleiben frei konfigurierbar.
- Profilabhängige Verbindungshinweise; vorhandene IPs, Ports und Geräte-IDs werden nicht überschrieben.
- Nicht implementiertes eigenes iHM-EMS-Profil aus der Auswahl entfernt; SH-Weiterleitung bleibt nutzbar.
- Bestehender geprüfter SH-Schreibpfad und gespeicherte Geräte bleiben erhalten.


## V0.1.2

- Kommunikationsprüfungen zeigen Fortschritt, Ziel und Ergebnis direkt unter dem Prüfen-Knopf.
- Fehler schon beim Verbinden oder Erkennen des WR verschwinden nicht mehr oberhalb des sichtbaren Formulars.
- Hintergrund-Prüfauftrag mit kurzen Statusabfragen statt eines langen offenen Browseraufrufs; Statusabfragen schreiben keine Register.
- Laufende identische Prüfaufträge werden wiederverwendet; kein mehrfaches Test-Schreiben durch Statusabfragen.
- Konkreter Sperrgrund am Speichern-Knopf; Freigabe weiterhin nur nach erfolgreichem Schreiben und Rücklesen.
- Verständliche Timeout-/Modbus-Fehler und zusätzliche Frontend-Regressionstests.


## V0.1.1

- WR-Kommunikationsprüfung und Speichern sind unabhängig von Wetterstandort, DWD-Sensor und HA-Verbindung.
- Eigenständiger Bereich „Wetter & Standort“ für alle Wechselrichter.
- Vollständige Adresse lokal eingeben; nur Ortsname und Warngebietskennung werden direkt beim DWD abgefragt.
- Keine externe Adress-Geokodierung; Gemeinde, Kreis und Bundesland werden vor dem Speichern angezeigt.
- Direkte DWD-Abfrage alle fünf Minuten; Berücksichtigung der Gültigkeitszeiten und Schutz bei fehlenden/fehlerhaften Daten.
- Bestehende Geräte/Kategorien bleiben erhalten; alte DWD-Sensorzuordnung am WR entfällt.


## V0.1.0

Erste Version als Home-Assistant-App (Add-on), installierbar direkt über den
App-Store mit diesem Repository.

- Eigene deutsche Weboberfläche über „Weboberfläche öffnen“ / Ingress.
- Mehrere Wechselrichter und dauerhaft gespeicherte Kategorien unter /data.
- Exklusive Monatsauswahl mit Normal- und DWD-Backup-SOC je Kategorie.
- Kommunikationsprüfung mit Modell-/Seriennummernerkennung und Lesen,
  einmaligem Schreiben desselben Werts und anschließendem Rücklesen.
- Speichern erst nach erfolgreicher Prüfung; Ergebnis in der Oberfläche.
- DWD-Ereignisse über die Home-Assistant-WebSocket-API.
- Monatswechsel um 00:05 Uhr in der Home-Assistant-Zeitzone.
- Einzelner Retry nach 30 Sekunden für fehlgeschlagene Betriebsänderungen.
- SH-Anbindung direkt an WR/WiNet; nativer iHomeManager-Backup-SOC bleibt
  ausdrücklich nicht unterstützt.

