
# Comprendre le Masque d'Horizon

Le **Masque d'Horizon** indique à Nova exactement où se trouvent les obstructions physiques dans votre emplacement spécifique. Il utilise une liste de points de coordonnées pour tracer une "ligne d'horizon" qui bloque certaines parties du ciel.

Chaque point dans la liste est une paire de nombres : `[Azimut, Altitude]`.

  * **Azimut (0-360) :** La direction de la boussole. 0 est le Nord, 90 est l'Est, 180 est le Sud, etc.
  * **Altitude (0-90) :** La hauteur de l'obstruction en degrés dans cette direction.

## Voir en action

Pour vous donner une meilleure idée, j'ai pris les données de ma propre jardinière (où je lutte contre une maison et de grands arbres) et je les ai visualisées.

![Exemple de Masque d'Horizon](/api/help/img/Horizonmask.jpeg)

Dans ce graphique :

  * La **Zone Marron** est le ciel bloqué défini par les coordonnées.
  * La **Ligne Rouge Pointillée** est le Seuil d'Altitude global (plus d'informations ci-dessous).
  * La **Zone Bleue** est votre véritable zone d'imagerie libre.

## Comment écrire votre masque

Les données sont entrées comme une simple liste de paires de coordonnées. Vous n'avez pas besoin d'être programmeur pour faire cela, suivez simplement le modèle !

**Le format des données :**

```text
[[Azimut, Altitude], [Azimut, Altitude], ...]
```

**Exemple de mon jardin :**
Voici les données brutes utilisées pour générer le graphique ci-dessus. Vous pouvez copier cette structure et changer les nombres pour correspondre à votre ciel :

```text
[[0.0, 0.0], [30.0, 30.0], [60.0, 36.0], [80.0, 25.0], [83.0, 30.0], [85.0, 20.0],
[88.0, 0.0], [120.0, 30.0], [130.0, 20.0], [132.0, 0.0]]
```

### Règles clés pour un bon masque

1.  **Les points se connectent automatiquement :** Nova trace une ligne droite entre chaque point que vous listez. Si vous définissez un point à `[88, 0]` et le suivant à `[120, 30]`, cela crée une pente les connectant.
2.  **Utilisez "0" pour briser les obstructions :** Comme les points se connectent, vous devez ramener l'altitude à `0.0` pour "terminer" une obstruction.
      * *Remarquez dans l'exemple :* Je termine le premier grand bloc à `[88.0, 0.0]` puis je commence le prochain pic.
3.  **Vous n'avez pas besoin des 360 complets :** Vous n'avez pas besoin de commencer à 0 ou de finir à 360. Si vous n'avez qu'un grand arbre entre l'Azimut 140 et 160, vous avez juste besoin d'ajouter des points pour cette zone spécifique. Le reste du ciel restera dégagé par défaut.

## Importer depuis Stellarium

Si vous utilisez Stellarium et avez un fichier d'horizon `.hzn` ou `.txt`, vous pouvez l'importer directement au lieu de taper les données manuellement.

1. Cliquez sur le bouton **Import .hzn** sous la zone de texte du Masque d'Horizon.
2. Sélectionnez votre fichier d'horizon Stellarium (`.hzn` ou `.txt`).
3. Le fichier est analysé automatiquement et le champ Masque d'Horizon est rempli avec les données converties.

Les lignes de commentaire (commençant par `#` ou `;`) sont ignorées. Si le fichier contient plus de 100 points de données, il est automatiquement simplifié pour garder les données légères. Les valeurs sont arrondies à une décimale et triées par azimut.

## Le "Temps Observable Net"

Vous remarquerez peut-être un paramètre dans votre configuration appelé **Seuil d'Altitude** (par défaut 20 degrés - vous pouvez le définir sous "Général").

  * **Seuil d'Altitude :** C'est la hauteur minimale globale qu'un objet doit atteindre pour être considéré bon pour l'imagerie (pour éviter l'atmosphère épaisse/brume près de l'horizon).
  * **Masque d'Horizon :** Cela découpe des morceaux spécifiques de ciel *au-dessus* de ce seuil.

Nova combine ces deux intelligences. Il calcule le **Temps Observable Net** - ce qui signifie qu'il ne compte que le temps où l'objet est au-dessus de votre limite globale de 20° **ET** non caché derrière les formes spécifiques de votre Masque d'Horizon.
