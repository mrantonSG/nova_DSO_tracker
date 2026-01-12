
---
## Nova DSO Tracker - Version 3.0.0 Release Notes

We're excited to announce the release of Nova DSO Tracker v3.0.0! This is a major update focused on transforming the application into a powerful planning tool, with a completely redesigned configuration interface and new features to help you get the most out of your imaging sessions.

###  Major New Features

* **The Outlook Tab**
    A powerful new planning tool that automatically finds the best imaging opportunities for your designated **Project** targets. The Outlook worker runs in the background to score potential nights based on key criteria, including object altitude, observable duration, moon phase, and moon separation, helping you plan your sessions for weeks or months in advance.

* **Redesigned Configuration Page**
    The configuration page has been completely redesigned with a clean, intuitive **tabbed layout**. Settings for General, Locations, Objects, and the new Rigs are now neatly organized and easily accessible. The UI has been harmonized for a more consistent and professional feel.

* **Comprehensive Rig Management**
    This is a cornerstone of the new version, allowing you to precisely model your equipment:
    * **Component Database:** Define and save your individual pieces of equipment, including **Telescopes**, **Cameras**, and **Reducers/Extenders**.
    * **Rig Builder:** Combine your components to create and save complete imaging rigs.
    * **Automatic Calculations:** Nova automatically calculates key performance metrics for each rig, including **Effective Focal Length**, **f/ratio**, **Field of View (FOV)**, and **Image Scale**.
    * **Interactive Sampling Calculator:** A new tool on the Rigs tab allows you to select your typical seeing conditions (FWHM) to get instant, color-coded feedback on whether your rigs are **oversampled, undersampled, or in the optimal range**. It even provides helpful tips, like suggesting 2x2 binning.

* **"Framing With Your Rigs" Assistant**
    The object details page (where the altitude graph is shown) now includes a "Framing With Your Rigs" section. This table gives you an at-a-glance assessment of how well a target will fit in each of your rig's fields of view, with helpful categories like "Good Fit," "Wide Field," "Fits with Rotation," and "Mosaic Required."

* **Automatic Update Notifications**
    The application will now subtly notify you in the header if a newer version is available on GitHub. The message provides a direct link to the latest release page so you can stay up-to-date.

###  Improvements & Bug Fixes

* Completely refactored the startup cache-warming process to be more robust, performant, and reliable, especially for multi-user installations on servers like a Raspberry Pi.
* Fixed a critical bug in multi-user environments where different users' cache files could overwrite each other. All cache files are now user-specific.
* Standardized UI elements like buttons, form layouts, and flash messages for a more consistent look and feel across the application.
* Improved form handling and redirection logic for a smoother user experience when adding or editing data.

---
Thank you for using Nova DSO Tracker! We hope these new planning and configuration features help you capture some amazing images.