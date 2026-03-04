
#### Configuration des Rigs

L'onglet **Rigs** est l'endroit où vous définissez votre équipement d'imagerie. Bien que cela puisse ressembler à une simple saisie de données, cette configuration est **critique** pour débloquer toute la puissance de Nova App.

**Pourquoi les Rigs sont importants**

* **Recadrage & Mosaïques :** L'outil de Recadrage visuel repose entièrement sur vos définitions de Rig pour dessiner des rectangles de capteur précis. **Sans Rig sauvegardé, l'outil de Recadrage et le Planificateur de Mosaïque ne fonctionneront pas.**
* **Rapports de Journal :** Vos journaux d'observation sont directement liés à ces Rigs. Les définir ici garantit que vos futurs Rapports de Journal incluent automatiquement des spécifications techniques détaillées (comme la longueur focale et l'échelle de pixel) sans que vous ayez à les saisir à chaque fois.

**1. Définissez vos composants**

Avant de pouvoir construire un rig complet, vous devez définir les pièces individuelles d'équipement dans votre inventaire.

* **Télescopes :** Entrez l'Ouverture et la Longueur Focale (en mm).
* **Caméras :** Entrez les Dimensions du Capteur (mm) et la Taille des Pixels (microns). Ces données sont essentielles pour calculer votre champ de vision.
* **Réducteurs / Extendeurs :** Entrez le facteur optique (par ex. `0.7` pour un réducteur, `2.0` pour une Barlow).

**2. Configurez vos Rigs**

Une fois vos composants ajoutés, combinez-les en un système d'imagerie fonctionnel.

* **Créer un Rig :** Donnez un surnom à votre configuration (par ex. "Redcat Grand Champ") et sélectionnez le Télescope, la Caméra et le Réducteur optionnel spécifiques dans les menus déroulants.
* **Statistiques automatiques :** Nova calcule instantanément votre **Longueur Focale Effective**, **Ouverture** et **Échelle d'image** (arcsec/pixel).

**Analyse d'échantillonnage**

Utilisez le menu déroulant **"Sélectionnez votre Seeing typique"** pour vérifier votre performance optique. Nova analysera votre Échelle d'image par rapport aux conditions locales du ciel et vous dira si votre configuration est **Sous-échantillonnée**, **Sur-échantillonnée** ou une correspondance parfaite.
