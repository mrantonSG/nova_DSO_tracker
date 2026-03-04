
#### Gestion des données & Sauvegarde

Ces trois boutons vous permettent d'enrichir vos données, de les sauvegarder ou de les transférer entre différentes instances de **Nova App** (par exemple, déplacer des données d'un ordinateur personnel vers un serveur cloud).

**1. Récupérer les détails manquants**

Si vous avez des objets dans votre bibliothèque avec des données manquantes (comme la Magnitude, la Taille ou la Classification), cliquez sur ce bouton.

* **Fonctionnement :** Nova analyse votre bibliothèque pour trouver les entrées incomplètes et interroge des bases de données astronomiques externes pour remplir automatiquement les blancs.
* **Note :** Ce processus peut prendre beaucoup de temps selon le nombre d'objets à mettre à jour.

**2. Télécharger (Sauvegarde)**

Cliquez sur le menu déroulant **Download ▼** pour exporter vos données dans des fichiers portables. C'est essentiel pour sauvegarder votre travail ou migrer vers un nouvel appareil.

* **Configuration :** Exporte vos Emplacements, Objets et Paramètres généraux (YAML).
* **Journal :** Exporte tous vos Projets et Journaux de session (YAML).
* **Rigs :** Exporte vos définitions de Télescope, Caméra et Rig (YAML).
* **Photos du Journal :** Télécharge une archive ZIP contenant toutes les images attachées à vos journaux d'observation.

**3. Importer (Restaurer & Transférer)**

Cliquez sur le menu déroulant **Import ▼** pour charger des données à partir d'un fichier de sauvegarde.

* **Flux de travail :** Sélectionnez le type de données que vous souhaitez charger (Config, Journal, Rigs ou Photos) et choisissez le fichier correspondant depuis votre ordinateur.
* **⚠️ Avertissement important :** L'importation est généralement une opération de **"Remplacement"**. Par exemple, l'importation d'un fichier de Configuration remplacera vos Emplacements et Objets actuels par ceux du fichier. Cela garantit que votre système correspond exactement à l'état de sauvegarde, ce qui est parfait pour restaurer des données ou synchroniser un serveur avec votre version locale.
