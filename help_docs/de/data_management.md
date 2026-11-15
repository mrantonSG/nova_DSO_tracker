
#### Datenverwaltung & Backup

Diese vier Schaltflächen ermöglichen es Ihnen, Ihre Daten anzureichern, zu sichern, zwischen verschiedenen **Nova App**-Instanzen zu übertragen (z. B. beim Umzug von einem Laptop auf einen Cloud-Server) oder eine lokale Nova und Ihr Server-Konto auf dem gleichen Stand zu halten.

**1. Fehlende Details abrufen**

Wenn Sie Objekte in Ihrer Bibliothek mit fehlenden Daten haben (wie Magnitude, Größe oder Klassifikation), klicken Sie auf diese Schaltfläche.

* **Funktionsweise:** Nova durchsucht Ihre Bibliothek nach unvollständigen Einträgen und fragt externe astronomische Datenbanken ab, um die Lücken automatisch zu füllen.
* **Hinweis:** Dieser Prozess kann je nach Anzahl der zu aktualisierenden Objekte lange dauern.

**2. Herunterladen (Backup)**

Klicken Sie auf das Dropdown-Menü **Herunterladen ▼**, um Ihre Daten in portable Dateien zu exportieren. Dies ist wichtig für die Sicherung Ihrer Arbeit oder die Migration auf ein neues Gerät.

* **Konfiguration:** Exportiert Ihre Standorte, Objekte und allgemeinen Einstellungen (YAML).
* **Journal:** Exportiert alle Ihre Projekte und Sitzungsprotokolle (YAML).
* **Rigs:** Exportiert Ihre Teleskop-, Kamera- und Rig-Definitionen (YAML).
* **Journal-Fotos:** Lädt ein ZIP-Archiv mit allen Bildern herunter, die an Ihre Beobachtungsprotokolle angehängt sind.

**3. Importieren (Wiederherstellen & Transfer)**

Klicken Sie auf das Dropdown-Menü **Importieren ▼**, um Daten aus einer Backupdatei zu laden.

* **Workflow:** Wählen Sie den Datentyp, den Sie laden möchten (Config, Journal, Rigs oder Fotos), und wählen Sie die entsprechende Datei von Ihrem Computer aus.
* **⚠️ Wichtiger Hinweis:** Der Import ist im Allgemeinen eine **"Ersetzen"-Operation**. Wenn Sie beispielsweise eine Konfigurationsdatei importieren, werden Ihre aktuellen Standorte und Objekte durch die in der Datei enthaltenen ersetzt. Dies stellt sicher, dass Ihr System genau dem Backup-Zustand entspricht – ideal zum Wiederherstellen von Daten oder zum Synchronisieren eines Servers mit Ihrer lokalen Version.

**4. Synchronisierung**

Klicken Sie auf **Synchronisierung**, um diese Nova und Ihr Konto auf app.nova-tracker.com auf dem gleichen Stand zu halten — Objekte, Standorte, Rigs, Projekte, Journal-Sitzungen, Fotos und Protokolldateien. Jeder Computer, der synchronisiert, braucht ein eigenes Token, sodass mehrere Computer einem Konto auf dem Server folgen können. Auf einer Mehrbenutzer-Installation erscheint **Mit einem Server synchronisieren** nur, wenn ein Administrator es eingeschaltet hat — siehe die Hilfe auf der Seite **Synchronisierung**.

* **Einrichtung:** Klicken Sie auf dem Server auf **Synchronisierung** und erstellen Sie ein Token unter **Diese Nova-Instanz als Server verwenden**. Es wird nur einmal angezeigt, kopieren Sie es also sofort. Klicken Sie auf dem anderen Computer auf **Synchronisierung**, geben Sie die **Server-URL** und das **Sync-Token** ein und klicken Sie auf **Verbinden**.
* **Ihre Daten liegen bisher nur auf diesem Computer?** Exportieren Sie sie mit **Herunterladen ▼**, importieren Sie diese YAML-Dateien auf dem Server und verbinden Sie sich erst danach: Ein Server ohne eigene Daten lehnt ein Herunterladen ab.
* **Erster Schritt:** **Vom Server herunterladen** ersetzt die Daten dieses Computers durch die des Servers. **Jetzt synchronisieren** bleibt deaktiviert, bis dieser Download abgeschlossen ist. Wenn dieser Computer bereits Daten enthält, fordert Nova Sie auf, REPLACE zu tippen. Eine lokale Nova überschreibt den Server nie mit ihrem gesamten Datensatz — nach diesem ersten Download sendet sie nur Ihre eigenen Änderungen.
* **Synchronisieren:** **Jetzt synchronisieren** sendet Ihre Änderungen hoch und zieht die Änderungen des Servers herunter. Lesen Sie zuerst die **Sync-Zusammenfassung**: **Hoch** und **Runter**, jeweils mit neuen, geänderten und gelöschten Einträgen, dazu die Dateien. Angewendet wird nichts, bevor Sie nicht auf **Anwenden** klicken; **Abbrechen** verwirft den Vorgang. Ein Sync, der auf einer der beiden Seiten einen großen Teil der Datensätze löschen würde, verlangt eine zweifache Bestätigung.
* **Auf beiden Seiten geändert?** Die jüngere Änderung gewinnt — auch dann, wenn eine Seite den Eintrag gelöscht hat — und bei Gleichstand gewinnt der Server. Die Zusammenfassung listet jeden dieser Einträge und welche Fassung behalten wurde: "… — Version dieses Computers behalten" oder "… — Serverversion behalten".
* **Backups:** Nova sichert die Daten dieses Computers vor jedem Download und jeder Synchronisierung. Die 5 jüngsten Sync-Backups werden behalten.
* **Trennen** entfernt die Kopplung von diesem Computer und löscht auf keiner der beiden Seiten etwas. Das Token bleibt auf dem Server gültig, bis Sie es dort **widerrufen**.
