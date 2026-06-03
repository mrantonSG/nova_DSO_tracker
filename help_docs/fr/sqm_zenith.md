# Comprendre le SQM Zénith

Le **SQM** (Sky Quality Meter) mesure la noirceur de ton ciel au zénith, en **mag/arcsec²**. Des valeurs plus élevées indiquent un ciel plus sombre — un site vraiment noir atteint environ 21,5–22, tandis qu'un ciel suburbain se situe autour de 19–20.

## Bortle vs. SQM

Si tu laisses le SQM Zénith vide, Nova dérive une valeur SQM nominale depuis ton paramètre **échelle de Bortle**. C'est une estimation raisonnable pour la plupart des usages de planification.

Si tu as une mesure SQM réelle d'un appareil ou d'une appli comme *Clear Outside*, entre-la ici. Nova utilisera ta valeur mesurée plutôt que l'estimation Bortle — cela améliore les calculs de magnitude limite et le score du classement IA.

## Valeurs typiques par classe Bortle

| Bortle | Type de ciel | SQM typique |
|--------|--------------|-------------|
| 1 | Vraiment sombre | ≥ 21,9 |
| 3 | Rural | ~21,5 |
| 5 | Périurbain | ~20,4 |
| 7 | Périurbain/urbain | ~19,1 |
| 9 | Centre-ville | ≤ 18,0 |

## Conseils

- Une seule mesure par nuit claire et sans lune au zénith suffit.
- Le SQM varie avec l'humidité, la fumée et l'airglow saisonnier — pas besoin de micro-précision. Une valeur à 0,2 mag de la réalité est largement suffisante.
- Si tu observes depuis plusieurs sites, chaque site peut avoir sa propre valeur SQM.
