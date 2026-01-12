**Release Date:** December 19, 2025

### Highlights

This release transforms Nova from a tracker into a visual discovery tool. The new **Inspiration Tab** provides a gallery view of currently observable targets, while **Custom Imagery** allows you to personalize your database with your own astrophotos. We have also overhauled the configuration system with **Advanced Object Management**, offering bulk actions and a powerful **Duplicate Manager** to keep your library clean.

Additionally, a highly requested **Dark Theme** is now available for better usability during imaging sessions.

---

### New Features

#### Visual Discovery & Inspiration

<img width="1618" height="1060" alt="Screenshot_46_inspire" src="https://github.com/user-attachments/assets/14441874-54a0-49ec-ab8d-844d70086dfc" />


* **Night Explorer (Inspiration Tab):** A new visual gallery displaying targets currently visible from your location. Instead of a data list, objects are presented as tiles prioritized by their current altitude, allowing you to browse potential targets visually.
* The easiest way to get started is to (re-) import the catalogs - they have been updated with image and text for the objects:

<img width="1491" height="538" alt="Screenshot 2025-12-19 at 11 36 38" src="https://github.com/user-attachments/assets/7fa53e9b-d899-47c7-b0e7-d177648a2b5f" />

* **Custom Imagery:** You can also link your own astrophotos directly to objects in your database. These images appear in the Inspiration tab and the new "Inspiration" sub-tab within the object detail view.

<img width="1618" height="1060" alt="Screenshot_46_tile_modal" src="https://github.com/user-attachments/assets/025c0d12-d648-434a-81a8-69f7272fba9f" />


#### Configuration & Management

* **Advanced Object Management:** The configuration interface has been overhauled to support bulk actions.
* **Filtering:** You can now filter objects by their catalog source (e.g., "Messier") or other criteria such as magnitude or size.
* **Bulk Enable/Disable:** Select multiple items to enable or disable them from calculations without deleting them from your database.

<img width="1618" height="1060" alt="Screenshot_46_object_list" src="https://github.com/user-attachments/assets/428dd3d1-de3f-460d-9d85-ce5f6626cf26" />


* **Duplicate Management:** A dedicated tool scans your database for objects with coordinates within 2.5 arcminutes of each other (e.g., M101 and NGC 5457). You can review these pairs and merge them into a single entry, preserving your projects and journals.
* **Dark Theme:** The interface now supports a dark mode to reduce eye strain during night imaging sessions.
* **Contextual Help:** Help badges (?) have been added throughout the application to provide immediate documentation for specific features.

<img width="1618" height="1060" alt="Screenshot_46_night" src="https://github.com/user-attachments/assets/aacbfdf1-2b5d-4ddd-b1cf-ef485aabd62d" />


---

### Improvements & Fixes

* **Performance Optimization:** Database queries for Horizon Points are now eager-loaded, improving page load times for users with complex horizon masks.
* **Object Normalization:** Enhanced the logic for object name normalization to handle formats like "SH2-155" versus "SH 2-155" more consistently.

---

### Upgrade Notes

This version adds several new columns to the `astro_objects` table to support curation fields (images, credits).

* **No manual action is required.** Upon restarting the container/application, Nova will automatically detect the schema changes and apply the necessary patches safely.