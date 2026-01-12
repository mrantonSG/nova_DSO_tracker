# Nova DSO Tracker v4.0.8

This is a significant stability and reliability release for Nova DSO Tracker. The primary focus of v4.0.8 has been on improving the accuracy of our astronomical calculations and implementing a comprehensive new testing framework to ensure future reliability.

This update addresses several key bugs, especially those affecting users at high latitudes and calculations for circumpolar objects.

---

## Core Calculation & Accuracy Fixes

I've overhauled parts of the calculation engine to provide more accurate data for all users:

* **High Latitude Calculations:** Fixed a critical bug where calculations would fail for locations in high latitudes (e.g., inside the Arctic Circle) during periods with no astronomical night. The planner will now correctly handle these "all-day-light" or "all-night-dark" scenarios.
* **Circumpolar Object Opportunities:** Corrected an error in the opportunity calculation for circumpolar objects, which previously resulted in incorrect observable durations.
* **Max Altitude Calculation:** Fixed a bug where objects that never rise (resulting in a negative maximum altitude) were incorrectly displaying their max altitude as 0. The calculations now correctly handle all object paths.

---

## Bug Fixes & Quality of Life

* **New Object Normalization:** A new, more robust object name normalization engine has been implemented. This will significantly improve data consistency, especially when linking journal entries to objects (e.g., "SH2129" is now correctly normalized to "SH 2-129").
* **Horizon Mask Import:** Fixed an issue where importing a YAML config file could cause horizon mask data to be duplicated.
* **UI Location Refresh:** Squashed a UI bug where the main object list would not refresh to match a new location if the location was changed while the list was still loading.

---

## Developer & Stability

* **New Testing Framework:** This is the biggest change under the hood. We have added dozens of new unit and integration tests. This new test suite now covers:
    * Core API endpoints (data, weather, plotting)
    * All major user actions (adding objects, saving notes, uploading images)
    * Multi-user authentication logic
    * Critical CLI maintenance commands
    * Database portability and data integrity

This new framework allowed me to find and fix the bugs listed above and will make the application much more stable and easier to maintain.

---

Thank you for using Nova DSO Tracker!