
#### Assistant de Recadrage

L'**Assistant de Recadrage** est un outil visuel puissant qui vous permet de prévisualiser exactement comment votre cible apparaîtra à travers votre caméra. Il superpose le champ de vision (FOV) de votre équipement sur une image d'arpentage du ciel professionnelle.

**Pour commencer**

1.  **Sélectionner un Rig :** Utilisez le menu déroulant en haut pour choisir l'un de vos profils d'équipement préconfigurés. Le rectangle à l'écran représente le champ de vision de votre capteur.
2.  **Déplacer & Centrer :**
    * **Clic & Glisser :** Par défaut, **Verrouiller FOV** est activé. Cela signifie que le rectangle du capteur reste fixe au centre de votre écran, tandis que le déplacement de la souris déplace le ciel *derrière* lui. Cela simule comment votre télescope se déplace dans le ciel pour cadrer la cible.
    * **Contrôles de réglage fin :** (Verrouiller FOV désactivé) Utilisez les boutons fléchés (↑ ↓ ← →) dans la barre d'outils (ou les touches fléchées de votre clavier) pour effectuer des réglages précis par étapes d'une minute d'arc.
    * **Recentrer :** Cliquez sur "Recentrer sur l'objet" pour ramener la vue aux coordonnées de catalogue de la cible.

**Contrôles de composition**

* **Rotation :** Utilisez le curseur pour faire pivoter l'angle de votre caméra (0-360°).
    * **Compatibilité ASIAIR :** L'angle affiché ici correspond exactement à l'angle "Framing Support" dans l'application **ASIAIR**, ce qui facilite la réplication de votre plan sur le terrain.
    * *Astuce :* Appuyez sur le texte de l'angle à côté du curseur pour le réinitialiser rapidement à 0°.
* **Ceinture Géo :** Cochez cette case pour superposer la Ceinture des Satellites Géostationnaires (affichée comme une ligne pointillée violette).
    * **Objectif :** Les satellites géostationnaires restent fixes par rapport au sol, ce qui signifie qu'ils créeront des traînées sur vos images alors que votre télescope suit les étoiles. Utilisez cette superposition pour vous assurer que votre cadrage ne se trouve pas directement sur cette "autoroute satellite".
    * **Précision :** La position de la ligne est automatiquement calculée et corrigée pour la parallaxe en fonction de la latitude de votre site.
* **Arpentages :** Changez la source de l'image d'arrière-plan en utilisant le menu déroulant "Survey".
    * **DSS2 (Couleur) :** Bonne vue optique polyvalente.
    * **H-alpha :** Excellent pour voir la structure des nébuleuses faibles.
    * **Mode Fusion :** Vous pouvez fusionner un deuxième arpentage (comme H-alpha) sur l'image couleur de base en utilisant le menu déroulant "Fusionner avec" et le curseur d'opacité. Cela aide à révéler des détails cachés tout en gardant les couleurs des étoiles visibles.

**Planificateur de Mosaïque**

Si votre cible est trop grande pour une seule image, utilisez la section **Mosaïque** dans la barre d'outils.

1.  Définissez le nombre de **Colonnes** et de **Lignes** (par ex. 2x1 pour un panorama large).
2.  Ajustez le **Chevauchement %** (par défaut 10%).
3.  **Copier le Plan :** Cliquez sur "Copier le Plan (CSV)" pour générer une liste de coordonnées compatible avec les logiciels d'acquisition comme **ASIAIR** ou **N.I.N.A.**.

**Sauvegarder votre travail**

* **Sauvegarder le Recadrage :** Cliquez ici pour stocker votre Rig actuel, votre Rotation et vos Coordonnées centrales dans la base de données. La prochaine fois que vous visiterez cet objet, votre recadrage personnalisé sera automatiquement restauré.
* **Verrouiller FOV :** Ceci est coché par défaut. Le décocher déverrouille le rectangle du capteur, vous permettant de faire glisser le rectangle lui-même sur une carte du ciel statique.
