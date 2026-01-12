
***

## Nova DSO Tracker v3.6.0 – "Celestial Refinement"

This is a significant update focused on improving the user interface, workflow, and configuration management for both single and multi-user setups. I've overhauled major components to make planning and logging your sessions more intuitive and powerful.

---
###  What's New

#### Redesigned Graph & Details View
The main object detail screen has been completely redesigned with a clean, **tabbed layout**. Information is now neatly organized into three dedicated tabs, keeping your workspace tidy and focused:
* **Chart:** The classic altitude/azimuth graph.
* **Journal:** A brand-new, dedicated interface for your session history.
* **Framing & Notes:** All planning tools, notes, and imaging opportunities in one place.

####  Integrated Weather Forecast on Altitude Chart
The daily altitude chart is now more powerful than ever. It integrates a **72-hour weather forecast** directly onto the graph, showing bands for:
* **Cloud Cover:** From Clear to Overcast.
* **Astronomical Seeing:** From Excellent to Bad.
This allows you to see at a glance if a night with good altitude for your target also has promising weather.

####  Overhauled Journal UI & Experience
The session journal has been completely reimagined for a more efficient and insightful experience.
* **Two-Column Layout:** A persistent list of your session history for an object is now on the left, with full details appearing on the right.
* **Redesigned Summary Panel:** The details summary is no longer redundant. It now features prominent "stat cards" that highlight the most critical performance metrics of a session: **Guiding (RMS), Seeing (FWHM), Sky Quality (SQM), and Exposures**. This gives you a much better at-a-glance comparison of your session quality.
* **Consistent Theming:** The UI now uses your app's standard green for active tabs and highlights.

####  Improved Multi-User Configuration
For administrators running multi-user instances, key global settings have been moved out of individual user profiles for central management.
* **Moved to `.env`:** **Calculation Precision** and **Anonymous Telemetry** are now controlled via the `.env` file.
* **Smart Defaults:** If these settings are not present in the `.env` file, the system defaults to 15-minute precision and telemetry on.
* **Cleaner UI:** These options are now hidden from the user-facing Configuration page when in multi-user mode, simplifying the experience for your users.

---
###  Other Improvements & Bug Fixes
* **Improved:** Numerous visual refinements were made to the altitude charts, including legend placement and axis scaling for better consistency.
* **Improved:** The client-side JavaScript for the graph view has been refactored into a separate file for better performance and maintainability.

---
Thank you for your continued feedback and support in making Nova DSO Tracker better with every release!