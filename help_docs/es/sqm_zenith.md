# Entender el SQM Cenit

El **SQM** (Sky Quality Meter) mide la oscuridad de tu cielo en el cenit, en unidades de **mag/arcsec²**. Valores más altos significan cielos más oscuros — un lugar realmente oscuro alcanza alrededor de 21,5–22, mientras que un cielo suburbano puede estar entre 19–20.

## Bortle vs. SQM

Si dejas el SQM Cenit en blanco, Nova deriva un valor SQM nominal desde tu configuración de **escala Bortle**. Es una estimación razonable para la mayoría de los usos de planificación.

Si tienes una lectura SQM real de un medidor o una app como *Clear Outside*, introdúcela aquí. Nova usará tu valor medido en lugar de la estimación Bortle — esto mejora los cálculos de magnitud límite y la puntuación del clasificador IA.

## Valores típicos por clase Bortle

| Bortle | Tipo de cielo | SQM típico |
|--------|---------------|------------|
| 1 | Verdaderamente oscuro | ≥ 21,9 |
| 3 | Rural | ~21,5 |
| 5 | Suburbano | ~20,4 |
| 7 | Suburbano/urbano | ~19,1 |
| 9 | Centro urbano | ≤ 18,0 |

## Consejos

- Una sola lectura en una noche clara y sin luna en el cenit es suficiente.
- El SQM varía con la humedad, el humo y el airglow estacional — no te preocupes por la microprecisión. Un valor dentro de 0,2 mag de la realidad es más que suficiente.
- Si observas desde varias ubicaciones, cada una puede tener su propio valor SQM.
