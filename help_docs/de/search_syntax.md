
#### Haupt-Dashboard

Dies ist Ihre Missionskontrolle. Das Haupt-Dashboard gibt Ihnen einen Echtzeit-Überblick über Ihre Zielbibliothek, berechnet für Ihren aktuellen Standort und Ihre aktuelle Zeit. Es ist darauf ausgelegt, die Frage zu beantworten: *"Was ist jetzt am besten zu fotografieren?"*

**Sichtbarkeitshinweis**
Standardmäßig sind Objekte, die von Ihrem aktuellen Standort geometrisch unmöglich zu sehen sind (d. h. sie steigen nie über Ihre konfigurierte Horizontschwelle), **ausgeblendet**, um die Liste sauber zu halten. Diese Objekte erscheinen sofort wieder, wenn Sie explizit nach Namen oder ID suchen.

**Datenspalten**

* **Höhe/Azimut:** Aktuelle Echtzeit-Position.
* **23 Uhr:** Position um 23 Uhr heute Nacht, hilft Ihnen bei der Planung der Kern-Bildgebungsstunden.
* **Trend:** Zeigt, ob das Objekt steigt (↑) oder fällt (↓).
* **Max Höhe:** Der höchste Punkt, den das Objekt heute Nacht erreicht.
* **Beobachtbare Zeit:** Gesamtminuten, die das Objekt über Ihrer konfigurierten Horizontgrenze liegt.

**Erweiterte Filterung**

Die Filterzeile unter den Kopfzeilen ist mächtig. Sie können spezielle Operatoren verwenden, um Ihre Liste zu verfeinern:

* **Textsuche:** Tippen Sie normal, um Übereinstimmungen zu finden (z. B. `M31`, `Nebel`). Beachten Sie, dass die Suche nach einem spezifischen Objekt die Einstellung "unsichtbare Objekte ausblenden" überschreibt.
* **Numerische Vergleiche:**
* `>50`: Passt auf Werte größer als 50.
* `<20`: Passt auf Werte kleiner als 20.
* `>=` / `<=`: Größer/Kleiner oder gleich.
* **Bereiche (UND-Logik):** Kombinieren Sie Operatoren, um Werte innerhalb eines spezifischen Fensters zu finden.
* Beispiel: `>140 <300` in der *Azimut*-Spalte findet Objekte, die aktuell im südlichen Himmel sind (zwischen 140° und 300°).
* **Ausschluss (NICHT-Logik):** Beginnen Sie mit `!`, um Elemente auszuschließen.
* Beispiel: `!Galaxie` in der *Typ*-Spalte blendet alle Galaxien aus.
* Beispiel: `!Cyg` in *Sternbild* blendet Ziele im Schwan aus.
* **Mehrere Begriffe (ODER-Logik):** Trennen Sie Begriffe mit Kommas.
* Beispiel: `M31, M33, M42` in *Objekt* zeigt nur diese drei Ziele.
* Beispiel: `Nebel, Haufen` in *Typ* zeigt sowohl Nebel als auch Haufen.

**Gespeicherte Ansichten**

Sobald Sie einen nützlichen Filtersatz erstellt haben (z. B. "Galaxien hoch im Süden"), klicken Sie auf die Schaltfläche **Speichern** neben dem "Gespeicherte Ansichten"-Dropdown. Sie können diese Ansicht benennen und später sofort abrufen.

**Visuelle Entdeckung**

Der **Inspiration**-Tab bietet eine grafische Möglichkeit, potenzielle Ziele zu durchsuchen. Anstatt einer Datentabelle zeigt er:

* **Intelligente Vorschläge:** Die App hebt automatisch "Top-Auswahl" hervor – Objekte, die aktuell gut positioniert sind (hohe Höhe) und eine lange beobachtbare Dauer für die Nacht haben.
* **Visuelle Karten:** Jedes Ziel wird als Karte mit Bild, Beschreibung und Schlüsselstatistiken (Max Höhe, Dauer) auf einen Blick angezeigt.
* **Interaktive Details:** Klicken Sie auf eine beliebige Karte, um vollständige Details zu sehen oder direkt zu den Diagrammen zu springen.

**Tabs**

* **Position:** Echtzeit-Koordinaten und Sichtbarkeit.
* **Eigenschaften:** Statische Daten wie Magnitude, Größe und Sternbild.
* **Ausblick:** Eine langfristige Prognose, die die besten Nächte zum Fotografieren Ihrer aktiven Projekte zeigt.
* **Heatmap:** Ein visueller Jahreskalender, der zeigt, wann Objekte sichtbar sind.
* **Inspiration:** Eine visuelle Galerie aktuell sichtbarer Ziele mit Bildern und Zusammenfassungen.
* **Journal:** Eine Schnellzugriffsliste aller Ihrer aufgezeichneten Sitzungen.
