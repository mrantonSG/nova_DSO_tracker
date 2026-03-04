
### Allgemeine Einstellungen

Der **Allgemein**-Tab ermöglicht es Ihnen, die Basisregeln zu definieren, die die **Nova App** verwendet, um die Sichtbarkeit zu berechnen und gute Bildgelegenheiten zu identifizieren.

#### Sichtbarkeitsgrundlagen
* **Höhenschwelle (°):** Dies ist Ihr "Horizontboden". Objekte unter diesem Winkel (in Grad) gelten als behindert oder zu niedrig zum Fotografieren. Eine Einstellung von 20° oder 30° ist Standard, um atmosphärische Turbulenzen in Horizontnähe zu vermeiden.

#### Ausblick & Bildgebungskriterien
Diese Einstellungen bestimmen, welche Ziele in Ihrer "Ausblick"-Prognose erscheinen. Die App verwendet diese Regeln, um Nächte herauszufiltern, die Ihren Qualitätsstandards nicht entsprechen.

* **Min Beobachtbar (min):** Die Mindestzeit, die ein Objekt über Ihrer Schwelle sichtbar sein muss, um als gültige Gelegenheit zu gelten.
* **Min Max Höhe (°):** Die maximale Höhe, die ein Objekt während der Nacht erreichen muss. Wenn ein Objekt nie darüber aufsteigt, überspringt Nova es.
* **Max Mond-Beleuchtung (%):** Verwenden Sie dies, um Nächte herauszufiltern, in denen der Mond zu hell ist. (z. B. auf 20% setzen, um nur dunkle Nächte zu sehen).
* **Min Mond-Abstand (°):** Die Mindestdistanz zwischen Ihrem Ziel und dem Mond.
* **Suchmonate:** Wie weit in die Zukunft die Ausblick-Funktion Gelegenheiten berechnen soll (Standard ist 6 Monate).

#### Systemleistung
*(Hinweis: Diese Optionen sind nur im Einzelbenutzermodus verfügbar)*

* **Berechnungspräzision:** Steuert, wie oft Nova die Position eines Objekts berechnet, um Höhenkurven zu zeichnen.
    * **Hoch (10 min):** Glatteste Kurven, aber langsamer zu laden.
    * **Schnell (30 min):** Schnellere Ladezeiten, ideal für leistungsschwache Geräte (wie einen Raspberry Pi).
* **Anonyme Telemetrie:** Wenn aktiviert, sendet die **Nova App** einen winzigen, anonymen "Heartbeat" mit grundlegenden Systeminformationen (z. B. App-Version, Objektanzahlen). Es werden niemals persönliche Daten gesammelt. Dies hilft dem Entwickler zu verstehen, wie die App verwendet wird, um zukünftige Updates zu verbessern.
