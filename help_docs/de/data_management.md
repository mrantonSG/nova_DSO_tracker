
#### Datenverwaltung & Backup

Diese drei Schaltflächen ermöglichen es Ihnen, Ihre Daten anzureichern, zu sichern oder zwischen verschiedenen **Nova App**-Instanzen zu übertragen (z. B. beim Umzug von einem Laptop auf einen Cloud-Server).

**1. Fehlende Details abrufen**

Wenn Sie Objekte in Ihrer Bibliothek mit fehlenden Daten haben (wie Magnitude, Größe oder Klassifikation), klicken Sie auf diese Schaltfläche.

* **Funktionsweise:** Nova durchsucht Ihre Bibliothek nach unvollständigen Einträgen und fragt externe astronomische Datenbanken ab, um die Lücken automatisch zu füllen.
* **Hinweis:** Dieser Prozess kann je nach Anzahl der zu aktualisierenden Objekte lange dauern.

**2. Herunterladen (Backup)**

Klicken Sie auf das Dropdown-Menü **Download ▼**, um Ihre Daten in portable Dateien zu exportieren. Dies ist wichtig für die Sicherung Ihrer Arbeit oder die Migration auf ein neues Gerät.

* **Konfiguration:** Exportiert Ihre Standorte, Objekte und allgemeinen Einstellungen (YAML).
* **Journal:** Exportiert alle Ihre Projekte und Sitzungsprotokolle (YAML).
* **Rigs:** Exportiert Ihre Teleskop-, Kamera- und Rig-Definitionen (YAML).
* **Journal-Fotos:** Lädt ein ZIP-Archiv mit allen Bildern herunter, die an Ihre Beobachtungsprotokolle angehängt sind.

**3. Importieren (Wiederherstellen & Transfer)**

Klicken Sie auf das Dropdown-Menü **Import ▼**, um Daten aus einer Backupdatei zu laden.

* **Workflow:** Wählen Sie den Datentyp, den Sie laden möchten (Config, Journal, Rigs oder Fotos), und wählen Sie die entsprechende Datei von Ihrem Computer aus.
* **⚠️ Wichtiger Hinweis:** Der Import ist im Allgemeinen eine **"Ersetzen"-Operation**. Wenn Sie beispielsweise eine Konfigurationsdatei importieren, werden Ihre aktuellen Standorte und Objekte durch die in der Datei enthaltenen ersetzt. Dies stellt sicher, dass Ihr System genau dem Backup-Zustand entspricht – ideal zum Wiederherstellen von Daten oder zum Synchronisieren eines Servers mit Ihrer lokalen Version.
