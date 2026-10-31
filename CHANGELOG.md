# Änderungsprotokoll

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

