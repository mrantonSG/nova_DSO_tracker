
# Comparaison d'objet secondaire

Le menu déroulant **Ajouter un objet** vous permet de comparer la courbe d'altitude d'un autre objet avec votre cible principale sur la vue graphique.

## Comment les objets sont classés

Les objets dans le menu déroulant sont **triés par durée observable totale** (la plus longue en premier). Cela vous aide à identifier rapidement quelles cibles actives sont visibles le plus longtemps ce soir.

## Critères de filtrage

Les objets apparaissent dans la liste uniquement s'ils répondent à **tous** les critres suivants :

1. **Projet actif** — L'objet doit être marqué comme Projet actif (case à cocher dans l'onglet Notes & Recadrage)
2. **Observable ce soir** — L'objet doit être au-dessus de votre masque d'horizon pendant l'obscurité astronomique
3. **Coordonnées valides** — L'objet doit avoir des coordonnées RA et DEC définies
4. **Pas le principal** — L'objet que vous consultez actuellement est exclu de la liste

## Limites

- Seuls les **20 premiers** objets par durée observable sont affichés
- Les objets avec **0 minutes** de temps observable sont exclus

## Affichage

- L'altitude de l'objet secondaire est affichée comme une **ligne magenta solide**
- Aucune ligne d'azimut n'est rendue pour l'objet secondaire (pour réduire l'encombrement du graphique)
- Sélectionner **Aucun** supprime la comparaison secondaire et restaure la vue par défaut

## Conseils

- Utilisez cette fonction pour planifier votre nuit en comparant plusieurs cibles
- Excellent pour décider entre des objets qui transitent à des moments différents
- Le titre de la page se met à jour pour afficher les deux noms d'objets lorsqu'une comparaison est active
