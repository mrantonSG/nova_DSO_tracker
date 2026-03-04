
#### Trouver & Fusionner les doublons

Au fil du temps, votre bibliothèque peut accumuler des entrées en double pour la même cible (par ex. vous avez importé "M 42" manuellement, mais aussi ajouté "NGC 1976" depuis un catalogue). Cet outil analyse votre base de données pour trouver et résoudre ces conflits.

**Comment ça fonctionne**

Nova App analyse toute votre bibliothèque d'objets pour trouver des paires d'objets situés à moins de **2,5 minutes d'arc** l'un de l'autre.

**Résoudre les doublons**

Lorsque des doublons sont trouvés, ils sont affichés côte à côte. Vous devez décider quelle version est le "Maître" (celui que vous gardez) et lequel est le doublon à fusionner et supprimer.

* **Garder A, Fusionner B :** L'objet A reste. L'objet B est supprimé, mais ses données sont déplacées vers A.
* **Garder B, Fusionner A :** L'objet B reste. L'objet A est supprimé, mais ses données sont déplacées vers B.

**Qu'advient-il de mes données ?**

La fusion est un **processus intelligent** conçu pour préserver votre historique. Lorsque vous fusionnez un objet :

* **Journaux :** Toutes les sessions d'imagerie liées à l'objet supprimé sont re-liées à l'objet "Gardé".
* **Projets :** Tous les projets actifs ou passés sont déplacés vers l'objet "Gardé".
* **Recadrages :** Les données de recadrage sauvegardées sont déplacées. (Note : Si *les deux* objets avaient un recadrage sauvegardé, le recadrage de l'objet "Gardé" est préservé).
* **Notes :** Les notes privées ne sont pas perdues ! Les notes de l'objet supprimé sont ajoutées en bas des notes de l'objet "Gardé".

**Astuce :** Il est généralement préférable de garder l'objet avec le nom le plus courant (par ex. Garder "M 31", Fusionner "Galaxie d'Andromède") pour faciliter les recherches futures.
