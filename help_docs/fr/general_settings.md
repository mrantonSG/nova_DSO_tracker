
### Paramètres généraux

L'onglet **Général** vous permet de définir les règles de base que **Nova App** utilise pour calculer la visibilité et identifier les bonnes opportunités d'imagerie.

#### Bases de la visibilité
* **Seuil d'altitude (°) :** C'est votre "plancher d'horizon". Les objets sous cet angle (en degrés) sont considérés comme obstrués ou trop bas pour être imagés. Le régler à 20° ou 30° est standard pour éviter la turbulence atmosphérique près de l'horizon.

#### Perspectives & Critères d'imagerie
Ces paramètres déterminent quelles cibles apparaissent dans vos prévisions "Perspectives". L'application utilise ces règles pour filtrer les nuits qui ne répondent pas à vos standards de qualité.

* **Min Observable (min) :** Le temps minimum pendant lequel un objet doit être visible au-dessus de votre seuil pour être considéré comme une opportunité valide.
* **Altitude Max Min (°) :** La hauteur maximale qu'un objet doit atteindre pendant la nuit. Si un objet ne dépasse jamais cette hauteur, Nova l'ignorera.
* **Max Illum Lune (%) :** Utilisez ceci pour filtrer les nuits où la lune est trop brillante. (par ex. régler à 20% pour ne voir que les opportunités de nuits sombres).
* **Min Sép Lune (°) :** La distance minimale autorisée entre votre cible et la Lune.
* **Mois de recherche :** Jusqu'où dans le futur la fonction Perspectives doit calculer les opportunités (par défaut 6 mois).

#### Performance système
*(Note : Ces options sont uniquement disponibles en Mode Utilisateur Unique)*

* **Précision de calcul :** Contrôle la fréquence à laquelle Nova calcule la position d'un objet pour tracer les courbes d'altitude.
    * **Élevée (10 min) :** Courbes les plus lisses, mais chargement plus lent.
    * **Rapide (30 min) :** Temps de chargement plus rapides, idéal pour les appareils à faible puissance (comme un Raspberry Pi).
* **Télémétrie anonyme :** Si activée, **Nova App** envoie un minuscule "pouls" anonyme contenant des informations système de base (par ex. version de l'application, nombre d'objets). Aucune donnée personnelle n'est jamais collectée. Cela aide le développeur à comprendre comment l'application est utilisée pour améliorer les futures mises à jour.
