# Étoile de calibration

Avant le guidage, PHD2 et ASIAIR doivent **calibrer** les axes de la caméra de guidage en déplaçant la monture en RA et Dec et en mesurant le mouvement stellaire résultant. Pour un étalonnage optimal, votre étoile guide doit répondre à deux critères:

1. **Près de l'équateur céleste**: Déclinaison comprise dans **±20°** autour de 0°, où les mouvements RA/Dec sont les plus orthogonaux
2. **Près du méridien**: Angle horaire dans **±1,5 heure**, ce qui minimise l'erreur de cône et le jeu en déclinaison de la monture

## Ce que ce widget offre

Ce widget trouve la **meilleure étoile brillante disponible** qui satisfait les deux critères pour votre emplacement et la date sélectionnée, puis affiche la fenêtre temporelle pendant laquelle cette étoile se trouve dans la zone de calibration utilisable.

## Utilisation

1. Réglez la **date** dans l'onglet Graphique sur votre nuit d'imagerie prévue
2. Le widget affiche l'étoile de calibration recommandée avec ses **coordonnées RA/Dec**
3. La **fenêtre de calibration** indique quand l'étoile se trouve dans la zone optimale
4. Dans ASIAIR, **pointez vers la RA/Dec indiquée** avant de démarrer votre calibration de guidage
5. Terminez la calibration, puis pointez vers votre cible d'imagerie

## Conseils

- Si aucune étoile n'est trouvée, essayez de sélectionner une autre date ou vérifiez que votre emplacement est correctement configuré
- Le bouton d'actualisation (↻) relance la recherche d'étoile pour la date actuelle
- Les étoiles plus brillantes (magnitude plus faible) sont préférées pour une détection plus fiable de l'étoile guide
