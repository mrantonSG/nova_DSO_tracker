
#### Tableau de bord principal

C'est votre centre de contrôle. Le tableau de bord principal vous donne une vue d'ensemble en temps réel de votre bibliothèque de cibles, calculée pour votre emplacement et votre heure actuels. Il est conçu pour répondre à la question : *"Qu'est-ce qui est préférable d'imager maintenant ?"*

**Note de visibilité**
Par défaut, les objets qui sont géométriquement impossibles à voir depuis votre emplacement actuel (c'est-à-dire qu'ils ne s'élèvent jamais au-dessus de votre seuil d'horizon configuré) sont **masqués** pour garder la liste propre. Ces objets réapparaîtront instantanément si vous les recherchez explicitement par nom ou ID.

**Colonnes de données**

* **Altitude/Azimut :** Position en temps réel actuelle.
* **23h :** Position à 23h ce soir, vous aidant à planifier les heures principales d'imagerie.
* **Tendance :** Montre si l'objet monte (↑) ou descend (↓).
* **Altitude Max :** Le point le plus haut que l'objet atteint ce soir.
* **Temps observable :** Minutes totales pendant lesquelles l'objet est au-dessus de votre limite d'horizon configurée.

**Filtrage avancé**

La ligne de filtre sous les en-têtes est puissante. Vous pouvez utiliser des opérateurs spéciaux pour affiner votre liste :

* **Recherche textuelle :** Tapez normalement pour trouver des correspondances (par ex. `M31`, `Nébuleuse`). Notez que rechercher un objet spécifique remplacera le paramètre "masquer les objets invisibles".
* **Comparaisons numériques :**
* `>50` : Correspond aux valeurs supérieures à 50.
* `<20` : Correspond aux valeurs inférieures à 20.
* `>=` / `<=` : Supérieur/Inférieur ou égal.
* **Plages (Logique ET) :** Combinez des opérateurs pour trouver des valeurs dans une fenêtre spécifique.
* Exemple : `>140 <300` dans la colonne *Azimut* trouve les objets actuellement dans le ciel sud (entre 140° et 300°).
* **Exclusion (Logique NON) :** Commencez par `!` pour exclure des éléments.
* Exemple : `!Galaxie` dans la colonne *Type* masque toutes les galaxies.
* Exemple : `!Cyg` dans *Constellation* masque les cibles dans le Cygne.
* **Plusieurs termes (Logique OU) :** Séparez les termes par des virgules.
* Exemple : `M31, M33, M42` dans *Objet* affiche uniquement ces trois cibles.
* Exemple : `Nébuleuse, Amas` dans *Type* affiche à la fois les nébuleuses et les amas.

**Vues sauvegardées**

Une fois que vous créez un ensemble de filtres utile (par ex. "Galaxies hautes dans le sud"), cliquez sur le bouton **Sauvegarder** à côté du menu déroulant "Vues sauvegardées". Vous pouvez nommer cette vue et la rappeler instantanément plus tard.

**Découverte visuelle**

L'onglet **Inspiration** offre une façon graphique de parcourir les cibles potentielles. Au lieu d'un tableau de données, il présente :

* **Suggestions intelligentes :** L'application met automatiquement en évidence les "Meilleurs choix" — objets actuellement bien positionnés (haute altitude) et ayant une longue durée observable pour la nuit.
* **Cartes visuelles :** Chaque cible est affichée comme une carte avec une image, une description et des statistiques clés (Altitude Max, Durée) en un coup d'œil.
* **Détails interactifs :** Cliquez sur n'importe quelle carte pour voir les détails complets ou accéder directement à ses graphiques.

**Onglets**

* **Position :** Coordonnées et visibilité en temps réel.
* **Propriétés :** Données statiques comme Magnitude, Taille et Constellation.
* **Perspectives :** Une prévision à long terme montrant les meilleures nuits pour imager vos projets actifs.
* **Heatmap :** Un calendrier annuel visuel montrant quand les objets sont visibles.
* **Inspiration :** Une galerie visuelle des cibles actuellement visibles avec images et résumés.
* **Journal :** Une liste d'accès rapide de toutes vos sessions enregistrées.
