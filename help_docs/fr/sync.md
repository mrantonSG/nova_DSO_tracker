### Synchronisation d'instances

Nova peut synchroniser vos données entre deux installations : l'une agit comme
**serveur** (le hub), l'autre se connecte comme **client**. C'est ainsi que vous
gardez un ordinateur de maison et un d'observatoire à jour.

#### Configurer le serveur

1. Sur la machine qui conservera vos données, ouvrez **Configuration → Sync**.
2. Sous **Utiliser cette instance Nova comme serveur**, créez un jeton pour chaque instance
   qui se connectera. Copiez-le immédiatement : seul un hash est conservé, il ne
   pourra donc jamais être réaffiché.

**Voie locale (YAML).** Un serveur refuse un téléchargement tant qu'il ne
contient que ses données de départ. Si vous déplacez une installation existante
vers le serveur, importez-y d'abord vos fichiers YAML (Configuration → Outils de
données → Importer). C'est ce qui rend le premier téléchargement possible.

#### Connecter un client

1. Sur l'autre machine, ouvrez **Configuration → Sync** puis
   **Synchroniser avec un serveur**.
2. Saisissez l'URL du serveur (par exemple `https://app.nova-tracker.com`) et le
   jeton créé là-bas, puis **Se connecter**. Le nom du compte serveur s'affiche
   et le jeton est stocké sur cette machine.

#### Télécharger depuis le serveur

La première synchronisation de chaque appairage est un **téléchargement** : il
remplace les données de cette machine par celles du serveur. Si cette machine a
déjà des données, vous devez saisir `REPLACE` pour confirmer — les fichiers
remplacés sont déplacés de côté, jamais supprimés. **Télécharger depuis le
serveur** réinitialise cette machine sur l'état du serveur à tout moment.

#### Synchroniser maintenant

Une fois un téléchargement terminé, **Synchroniser maintenant** envoie vos
modifications et récupère celles du serveur. Un résumé s'affiche avant toute
application ; vérifiez-le puis choisissez **Appliquer** ou **Annuler**. Une
synchronisation qui supprimerait une grande partie de vos données demande
toujours une seconde confirmation.

#### Déconnecter

**Déconnecter** efface l'appairage et le jeton stocké de cette machine.

#### Sauvegardes et restauration

Nova effectue une sauvegarde locale avant chaque téléchargement et
synchronisation, sous `instance/backups/sync/`. Pour restaurer : arrêtez
l'application, copiez le `app.db` sauvegardé sur `instance/app.db`, recopiez les
dossiers `uploads/` et `logs/` dans `instance/`, puis redémarrez l'application.
