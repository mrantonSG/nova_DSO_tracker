### Instanz-Synchronisierung

Nova kann Ihre Daten zwischen zwei Installationen abgleichen: eine fungiert als
**Server** (Hub), die andere verbindet sich als **Client**. So halten Sie einen
Heim- und einen Observatoriumsrechner auf demselben Stand.

#### Server einrichten

1. Öffnen Sie auf dem Rechner, der die Daten halten soll, **Konfiguration → Sync**.
2. Erstellen Sie unter **Diese Nova-Instanz als Server verwenden** ein Token für jede
   Instanz, die sich verbindet. Kopieren Sie das Token sofort — es wird nur ein
   Hash davon gespeichert, daher kann es nie wieder angezeigt werden.

**Lokaler Weg (YAML).** Ein Server verweigert einen Download, solange er nur
seine Ausgangsdaten enthält. Wenn Sie eine bestehende Installation auf den
Server verschieben, importieren Sie dort zuerst Ihre YAML-Dateien
(Konfiguration → Datenwerkzeuge → Import). Das ermöglicht den ersten Download.

#### Client verbinden

1. Öffnen Sie auf dem anderen Rechner **Konfiguration → Sync** und gehen Sie zu
   **Mit einem Server synchronisieren**.
2. Geben Sie die Server-URL (z. B. `https://app.nova-tracker.com`) und das dort
   erstellte Token ein und klicken Sie auf **Verbinden**. Der Kontoname des
   Servers wird angezeigt; das Token wird auf diesem Rechner gespeichert.

#### Vom Server herunterladen

Die erste Synchronisierung jeder Kopplung ist ein **Download**: Er ersetzt die
Daten dieses Rechners durch die des Servers. Hat dieser Rechner bereits Daten,
müssen Sie `REPLACE` eingeben, um zu bestätigen — die ersetzten Dateien werden
beiseite gelegt, nie gelöscht. **Vom Server herunterladen** setzt diesen
Rechner jederzeit auf den Serverstand zurück.

#### Jetzt synchronisieren

Nach einem abgeschlossenen Download sendet **Jetzt synchronisieren** Ihre
Änderungen nach oben und holt die Änderungen des Servers herunter. Vor dem
Anwenden wird eine Zusammenfassung angezeigt; prüfen Sie sie und wählen Sie
**Anwenden** oder **Abbrechen**. Eine Synchronisierung, die einen großen Teil
Ihrer Daten löschen würde, verlangt immer eine zweite Bestätigung.

#### Trennen

**Trennen** löscht die Kopplung und das gespeicherte Token von diesem Rechner.

#### Sicherungen und Wiederherstellung

Nova erstellt vor jedem Download und jeder Synchronisierung eine lokale
Sicherung unter `instance/backups/sync/`. Zum Wiederherstellen: App stoppen, die
gesicherte `app.db` über `instance/app.db` kopieren, die gesicherten Ordner
`uploads/` und `logs/` zurück nach `instance/` kopieren und die App neu starten.
