
#### Rigs-Konfiguration

Der **Rigs**-Tab ist der Ort, an dem Sie Ihre Bildgebungsausrüstung definieren. Obwohl es wie einfache Dateneingabe erscheint, ist dieses Setup **kritisch**, um die volle Leistung der Nova App freizuschalten.

**Warum Rigs wichtig sind**

* **Framing & Mosaiken:** Das visuelle Framing-Tool basiert vollständig auf Ihren Rig-Definitionen, um genaue Sensor-Rechtecke zu zeichnen. **Ohne gespeichertes Rig funktionieren das Framing-Tool und der Mosaik-Planer nicht.**
* **Journal-Berichte:** Ihre Beobachtungsprotokolle verlinken direkt auf diese Rigs. Die Definition hier stellt sicher, dass Ihre zukünftigen Journal-Berichte automatisch detaillierte technische Spezifikationen (wie Brennweite und Pixelskala) enthalten, ohne dass Sie sie jedes Mal eingeben müssen.

**1. Definieren Sie Ihre Komponenten**

Bevor Sie ein vollständiges Rig erstellen können, müssen Sie die einzelnen Ausrüstungsstücke in Ihrem Inventar definieren.

* **Teleskope:** Geben Sie die Apertur und Brennweite ein (in mm).
* **Kameras:** Geben Sie die Sensorabmessungen (mm) und Pixelgröße (Mikrometer) ein. Diese Daten sind wesentlich für die Berechnung Ihres Sichtfeldes.
* **Reducer / Extender:** Geben Sie den optischen Faktor ein (z. B. `0.7` für einen Reducer, `2.0` für eine Barlow).

**2. Konfigurieren Sie Ihre Rigs**

Sobald Ihre Komponenten hinzugefügt sind, kombinieren Sie sie zu einem funktionalen Bildgebungssystem.

* **Rig erstellen:** Geben Sie Ihrem Setup einen Spitznamen (z. B. "Redcat Weitwinkel") und wählen Sie das spezifische Teleskop, die Kamera und den optionalen Reducer aus den Dropdown-Menüs aus.
* **Automatische Statistiken:** Nova berechnet sofort Ihre **Effektive Brennweite**, **Blende** und **Bildskala** (Bogensekunden/Pixel).

**Sampling-Analyse**

Verwenden Sie das Dropdown-Menü **"Wählen Sie Ihr typisches Seeing"**, um Ihre optische Leistung zu prüfen. Nova wird Ihre Bildskala gegen lokale Himmelsbedingungen analysieren und Ihnen mitteilen, ob Ihr Setup **Untersampled**, **Oversampled** oder eine perfekte Übereinstimmung ist.
