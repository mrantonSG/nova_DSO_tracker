
## Version 4.0.6 (Hotfix)

This is a hotfix release that addresses two bugs, including a critical fix for broken image links after migration.

  * **Fixed:** A bug where Project Notes on the "Framing & Notes" tab would display incorrectly, outside of the editor field. (This was the fix in `4.0.5`).
  * **Fixed:** A critical bug where images uploaded to notes were saved with an absolute URL (e.g., `http://localhost:5001/...`). This caused images to break after importing data to a new server.
  * **New:** Images are now saved with a portable, relative URL (e.g., `/uploads/...`).
