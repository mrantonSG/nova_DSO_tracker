### Instance Sync

Nova can sync your data between two installations: one acts as the **server**
(the hub), the other connects as a **client**. This is how you keep a home and
an observatory computer in step.

#### Set up the server

1. On the machine that will hold your data, open **Configuration → Sync**.
2. Under **Use this Nova as a server**, create a token for each instance that
   will connect. Copy the token immediately — only a hash of it is stored, so
   it can never be shown again.

**Local-first route (YAML).** A server refuses a download while it holds nothing
beyond its starter data. If you are moving an existing installation onto the
server, import your YAML files there first (Configuration → Data Tools →
Import). That is what makes the first download possible.

#### Connect a client

1. On the other machine, open **Configuration → Sync** and go to
   **Sync with a server**.
2. Enter the server URL (for example `https://app.nova-tracker.com`) and the
   token you created there, then **Connect**. The server's account name is
   shown, and the token is stored on this machine.

#### Download from server

The first sync for every pairing is a **download**: it replaces this machine's
data with the server's. If this machine already has data you must type
`REPLACE` to confirm — the files it replaces are moved aside, never deleted.
Run **Download from server** any time to reset this machine to the server's
state.

#### Sync now

Once a download has completed, **Sync now** sends your changes up and pulls the
server's changes down. A summary is shown before anything is applied; review it
and choose **Apply** or **Abort**. A sync that would delete a large part of your
data always asks for a second confirmation.

#### Disconnect

**Disconnect** clears the pairing and the stored token from this machine.

#### Backups and restore

Nova takes a local backup before every download and sync, kept under
`instance/backups/sync/`. To restore one: stop the app, copy the backed-up
`app.db` over `instance/app.db`, copy the backed-up `uploads/` and `logs/`
folders back into `instance/`, then start the app again.
