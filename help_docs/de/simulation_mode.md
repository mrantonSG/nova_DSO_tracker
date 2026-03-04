# Simulationsmodus

Der Simulationsmodus ermöglicht es Ihnen, Bildsitzungen für zukünftige Daten zu planen. Anstatt den Himmel so zu zeigen, wie er "jetzt" ist (Echtzeit), berechnet das Dashboard Objektpositionen, Mondphasen und Dunkelheitszeiten für das von Ihnen gewählte Datum.

### Verwendung
1.  **Schalter umschalten:** Klicken Sie auf den Schalter in der Statusleiste, um den Simulationsmodus zu aktivieren.
2.  **Datum auswählen:** Klicken Sie in das Datum-Eingabefeld, um Ihr Planungsdatum zu wählen.

### Was ändert sich?
* **Berechnungen:** Höhen, Azimute und Transitzeiten werden für die simulierte Nacht neu berechnet.
* **Visuelle Indikatoren:** Der Hintergrund der Statusleiste wird **rot**, und ein "Simuliert"-Badge erscheint neben dem Dashboard-Titel, um Sie daran zu erinnern, dass die Daten nicht Echtzeit sind.
* **Diagramme:** Höhendiagramme zeigen die Kurve für die ausgewählte Nacht.
* **Inspiration:** Die Mini-Diagramme im Inspiration-Modal spiegeln ebenfalls das simulierte Datum wider.

### Wichtige Hinweise
* **Tageszeit:** Die Simulation preserves Ihre aktuelle Wanduhr-*Tageszeit*. Wenn es jetzt 14:00 Uhr ist, zeigt das Dashboard Positionen für 14:00 Uhr am simulierten Datum.
* **Cache:** Das Wechseln von Daten aktualisiert automatisch den Berechnungs-Cache für dieses spezifische Datum.
