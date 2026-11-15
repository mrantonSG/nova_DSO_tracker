
#### Gestion des données & Sauvegarde

Ces quatre boutons vous permettent d'enrichir vos données, de les sauvegarder, de les transférer entre différentes instances de **Nova App** (par exemple, déplacer des données d'un ordinateur personnel vers un serveur cloud) ou de garder une instance Nova locale et votre compte serveur en phase.

**1. Récupérer les détails manquants**

Si vous avez des objets dans votre bibliothèque avec des données manquantes (comme la Magnitude, la Taille ou la Classification), cliquez sur ce bouton.

* **Fonctionnement :** Nova analyse votre bibliothèque pour trouver les entrées incomplètes et interroge des bases de données astronomiques externes pour remplir automatiquement les blancs.
* **Note :** Ce processus peut prendre beaucoup de temps selon le nombre d'objets à mettre à jour.

**2. Télécharger (Sauvegarde)**

Cliquez sur le menu déroulant **Télécharger ▼** pour exporter vos données dans des fichiers portables. C'est essentiel pour sauvegarder votre travail ou migrer vers un nouvel appareil.

* **Configuration :** Exporte vos Emplacements, Objets et Paramètres généraux (YAML).
* **Journal :** Exporte tous vos Projets et Journaux de session (YAML).
* **Rigs :** Exporte vos définitions de Télescope, Caméra et Rig (YAML).
* **Photos du Journal :** Télécharge une archive ZIP contenant toutes les images attachées à vos journaux d'observation.

**3. Importer (Restaurer & Transférer)**

Cliquez sur le menu déroulant **Importer ▼** pour charger des données à partir d'un fichier de sauvegarde.

* **Flux de travail :** Sélectionnez le type de données que vous souhaitez charger (Config, Journal, Rigs ou Photos) et choisissez le fichier correspondant depuis votre ordinateur.
* **⚠️ Avertissement important :** L'importation est généralement une opération de **"Remplacement"**. Par exemple, l'importation d'un fichier de Configuration remplacera vos Emplacements et Objets actuels par ceux du fichier. Cela garantit que votre système correspond exactement à l'état de sauvegarde, ce qui est parfait pour restaurer des données ou synchroniser un serveur avec votre version locale.

**4. Synchronisation**

Cliquez sur **Synchronisation** pour garder cette instance Nova et votre compte sur app.nova-tracker.com en phase — objets, emplacements, rigs, projets, sessions du journal, photos et fichiers journaux. Chaque ordinateur qui synchronise a besoin de son propre jeton, plusieurs ordinateurs peuvent donc suivre un même compte sur le serveur. Sur une installation multi-utilisateur, **Synchroniser avec un serveur** n'apparaît que si un administrateur a activé cette option — voir l'aide sur la page **Synchronisation**.

* **Préparation :** Sur le serveur, cliquez sur **Synchronisation** et créez un jeton sous **Utiliser cette instance Nova comme serveur**. Il n'est affiché qu'une seule fois, copiez-le immédiatement. Sur l'autre ordinateur, cliquez sur **Synchronisation**, saisissez l'**URL du serveur** et le **Jeton de synchronisation**, puis cliquez sur **Se connecter**.
* **Vos données ne sont que sur cet ordinateur pour l'instant ?** Exportez-les avec **Télécharger ▼**, importez ces fichiers YAML sur le serveur, puis connectez-vous seulement après : un serveur qui ne contient aucune donnée refuse un téléchargement.
* **Première étape :** **Télécharger depuis le serveur** remplace les données de cet ordinateur par celles du serveur. **Synchroniser maintenant** reste désactivé tant que ce téléchargement n'est pas terminé. Si cet ordinateur contient déjà des données, Nova vous demande de taper REPLACE. Une instance Nova locale n'écrase jamais le serveur avec tout son jeu de données — après ce premier téléchargement, elle n'envoie que vos propres modifications.
* **Synchronisation :** **Synchroniser maintenant** envoie vos modifications et récupère celles du serveur. Lisez d'abord le **Résumé de la synchronisation** : **Envoi** et **Réception**, chacun avec nouveaux, modifiés et supprimés, plus les fichiers. Rien n'est appliqué tant que vous n'avez pas cliqué sur **Appliquer** ; **Annuler** abandonne. Une synchronisation qui supprimerait une grande partie des enregistrements de l'un des deux côtés demande une double confirmation.
* **Modifié des deux côtés ?** La modification la plus récente gagne — y compris quand l'un des côtés a supprimé l'élément — et en cas d'égalité le serveur gagne. Le résumé liste chacun de ces éléments et la copie conservée : "… — version de cet ordinateur conservée" ou "… — version du serveur conservée".
* **Sauvegardes :** Nova sauvegarde les données de cet ordinateur avant chaque téléchargement et chaque synchronisation. Les 5 sauvegardes de synchronisation les plus récentes sont conservées.
* **Déconnecter** retire l'association de cet ordinateur et ne supprime rien, ni ici ni sur le serveur. Le jeton reste valide sur le serveur jusqu'à ce que vous le **révoquiez** là-bas.
