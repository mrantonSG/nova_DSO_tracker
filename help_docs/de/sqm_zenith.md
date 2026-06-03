# SQM Zenith verstehen

**SQM** (Sky Quality Meter) misst die Dunkelheit deines Himmels im Zenit, in der Einheit **mag/arcsec²**. Höhere Werte bedeuten dunkleren Himmel — ein wirklich dunkler Standort erreicht etwa 21,5–22, während ein städtischer Himmel bei 19–20 liegt.

## Bortle vs. SQM

Wenn du SQM Zenith leer lässt, leitet Nova einen nominellen SQM-Wert aus deiner **Bortle-Skala** ab. Das ist für die meisten Planungszwecke eine gute Näherung.

Wenn du einen gemessenen SQM-Wert von einem Messgerät oder einer App wie *Clear Outside* hast, trag ihn hier ein. Nova verwendet dann deinen Messwert statt der Bortle-Schätzung — das verbessert die Grenzgröße-Berechnung und das AI-Ranking.

## Typische Werte nach Bortle-Klasse

| Bortle | Himmeltyp | Typischer SQM |
|--------|-----------|---------------|
| 1 | Wirklich dunkel | ≥ 21,9 |
| 3 | Ländlich | ~21,5 |
| 5 | Vorstädtisch | ~20,4 |
| 7 | Vorstadt/Stadt | ~19,1 |
| 9 | Innenstadt | ≤ 18,0 |

## Tipps

- Eine einzelne Messung in einer klaren, mondlosen Nacht im Zenit reicht aus.
- SQM schwankt mit Luftfeuchtigkeit, Rauch und saisonalem Airglow — keine Mikropräzision nötig. Ein Wert innerhalb von 0,2 mag ist völlig ausreichend.
- Wenn du von mehreren Standorten beobachtest, kann jeder Standort seinen eigenen SQM-Wert haben.
