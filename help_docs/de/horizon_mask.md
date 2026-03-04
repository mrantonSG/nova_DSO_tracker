
# Den Horizont-Maske verstehen

Die **Horizont-Maske** sagt Nova genau, wo die physischen Hindernisse an Ihrem spezifischen Standort sind. Sie verwendet eine Liste von Koordinatenpunkten, um eine "Skyline" zu zeichnen, die Teile des Himmels ausblendet.

Jeder Punkt in der Liste ist ein Zahlenpaar: `[Azimut, Höhe]`.

  * **Azimut (0-360):** Die Kompassrichtung. 0 ist Norden, 90 ist Osten, 180 ist Süden, usw.
  * **Höhe (0-90):** Wie hoch das Hindernis in dieser Richtung in Grad ist.

## In Aktion sehen

Um Ihnen eine bessere Vorstellung zu geben, habe ich die Daten von meiner eigenen Garten-Säule (wo ich gegen ein Haus und einige hohe Bäume kämpfe) genommen und visualisiert.

![Horizont-Maske Beispiel](/api/help/img/Horizonmask.jpeg)

In diesem Diagramm:

  * Der **braune Bereich** ist der blockierte Himmel, der durch die Koordinaten definiert wird.
  * Die **rote gestrichelte Linie** ist die globale Höhenschwelle (mehr dazu unten).
  * Der **blaue Bereich** ist Ihre tatsächliche freie Bildgebungszone.

## Wie Sie Ihre Maske schreiben

Die Daten werden als einfache Liste von Koordinatenpaaren eingegeben. Sie müssen kein Programmierer sein, um dies zu tun – folgen Sie einfach dem Muster!

**Das Datenformat:**

```text
[[Azimut, Höhe], [Azimut, Höhe], ...]
```

**Mein Gartenbeispiel:**
Hier sind die Rohdaten, die verwendet wurden, um die Grafik oben zu erzeugen. Sie können diese Struktur kopieren und die Zahlen ändern, um Ihren Himmel anzupassen:

```text
[[0.0, 0.0], [30.0, 30.0], [60.0, 36.0], [80.0, 25.0], [83.0, 30.0], [85.0, 20.0],
[88.0, 0.0], [120.0, 30.0], [130.0, 20.0], [132.0, 0.0]]
```

### Wichtige Regeln für eine gute Maske

1.  **Punkte verbinden sich automatisch:** Nova zeichnet eine gerade Linie zwischen jedem Punkt, den Sie auflisten. Wenn Sie einen Punkt bei `[88, 0]` definieren und den nächsten bei `[120, 30]`, erzeugt es eine verbindende Steigung.
2.  **Verwenden Sie "0" zum Unterbrechen von Hindernissen:** Da sich die Punkte verbinden, müssen Sie die Höhe auf `0.0` zurücksetzen, um ein Hindernis zu "beenden".
      * *Beachten Sie im Beispiel:* Ich beende den ersten großen Block bei `[88.0, 0.0]` und starte dann den nächsten Gipfel.
3.  **Sie brauchen nicht die ganzen 360:** Sie müssen nicht bei 0 beginnen oder bei 360 enden. Wenn Sie nur einen großen Baum zwischen Azimut 140 und 160 haben, müssen Sie nur Punkte für diesen spezifischen Bereich hinzufügen. Der Rest des Himmels bleibt standardmäßig klar.

## Importieren aus Stellarium

Wenn Sie Stellarium verwenden und eine `.hzn`- oder `.txt`-Horizontdatei haben, können Sie diese direkt importieren, anstatt die Daten manuell einzugeben.

1. Klicken Sie auf die Schaltfläche **Import .hzn** unter dem Horizont-Maske-Textbereich.
2. Wählen Sie Ihre Stellarium-Horizontdatei (`.hzn` oder `.txt`).
3. Die Datei wird automatisch analysiert und das Horizont-Maske-Feld mit den konvertierten Daten gefüllt.

Kommentarzeilen (beginnend mit `#` oder `;`) werden ignoriert. Wenn die Datei mehr als 100 Datenpunkte enthält, wird sie automatisch vereinfacht, um die Daten leichtgewichtig zu halten. Werte werden auf eine Dezimalstelle gerundet und nach Azimut sortiert.

## Die "Netto-Beobachtungszeit"

Sie bemerken vielleicht eine Einstellung in Ihrer Konfiguration namens **Höhenschwelle** (Standard ist 20 Grad – Sie können sie unter "Allgemein" einstellen).

  * **Höhenschwelle:** Dies ist die globale Mindesthöhe, die ein Objekt erreichen muss, um als gut zum Fotografieren zu gelten (um dicke Atmosphäre/Dreck am Horizont zu vermeiden).
  * **Horizont-Maske:** Dies schneidet spezifische Himmelsstücke *über* dieser Schwelle aus.

Nova kombiniert diese beiden Intelligenzen. Es berechnet die **Netto-Beobachtungszeit** – das bedeutet, es zählt nur Zeit, in der das Objekt über Ihrer globalen 20°-Grenze **UND** nicht hinter den spezifischen Formen in Ihrer Horizont-Maske verborgen ist.
