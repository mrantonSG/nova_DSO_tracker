
***

## Nova DSO Tracker v3.5.0 - "The Horizon Update"

I'm excited to announce the release of Nova v3.5.0! This is a major update focused on providing more accurate, real-world planning tools and dramatically improving the overall robustness of the application. A huge thank you to our users for their invaluable feedback and rigorous testing that made this release possible.

---
## New Features

### Horizon Mask: See Your True Sky
You can now define custom horizon masks for each of your observing locations to account for real-world obstructions like trees, buildings, or mountains.

* **Configuration:** Add a `horizon_mask` list of `[azimuth, altitude]` pairs to any location in your configuration file. The order does not matter, and using an altitude of `0` will correctly revert to your baseline threshold.
* **Graph Visualization:** The mask is now drawn on the detailed altitude chart as a shaded area, giving you an instant visual of when your target is clear of obstructions.
* **Accurate Calculations:** All observability calculations—for the main table, imaging opportunities, and 11 PM status—now fully respect your custom horizon, providing a true-to-life assessment of your imaging windows.

### Single Sign-On for Multi-User Mode
For administrators running Nova in Multi-User (MU) mode, a new JWT-based Single Sign-On (SSO) endpoint has been added. This allows for seamless integration with external user authentication systems (e.g., a WordPress login).

---
## Improvements & Bug Fixes

This release includes a significant number of stability improvements and bug fixes based on user feedback.

#### Calculation Engine
* Fixed a critical bug where objects with very high transits ("steep curves") would not correctly interact with the horizon mask due to undefined azimuth values at the zenith.
* Increased the calculation resolution on the altitude graph (from 15-min to 5-min intervals) to prevent "jumping over" narrow obstructions.
* The "obstructed" (yellow) highlight on the main table is now more consistent, with "Current" and "11 PM" altitudes being checked and colored independently.
* Fixed a bug where `[az, 0]` in the horizon mask was being drawn literally instead of correctly reverting to the baseline altitude threshold.

#### Configuration & Robustness
* **Major Fix:** The application is now protected against crashes caused by an invalid or missing `default_location` in the config file. It will log a warning and fall back to the first available location.
* **Major Fix:** Resolved an issue where a malformed YAML configuration file (e.g., from a bad copy-paste or a missing hyphen) would cause the object list to appear empty. The app is now much more resilient to syntax errors.
* Fixed a bug that was causing in-memory configuration data to be accidentally modified by the calculation engine (the `[az, 0]` to `[az, 20]` issue).

#### User Interface
* Resolved a UI bug on the Configuration page where changes would not appear immediately after saving, requiring the user to navigate away and back. The form now reloads with the fresh data automatically.
* Fixed numerous CSS layout, alignment, and formatting issues on the Configuration page for a cleaner and more intuitive editing experience.
* The Horizon Mask can now be displayed in a compact horizontal list in the config editor.

---
Thank you again for your contributions. I hope these new features and fixes make your planning sessions more accurate and enjoyable. Clear skies!