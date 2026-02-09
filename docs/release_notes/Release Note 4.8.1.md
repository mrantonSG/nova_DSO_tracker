# Nova DSO Tracker v4.8.1

This is a maintenance update focused on tidying up a few loose ends and keeping our data connections reliable. While there aren't major new features, these changes should make your day-to-day planning a little smoother.

### Improvements & Logic Changes

* **Outlook Location Filter:** I updated the Outlook generation logic to only process locations marked as "Active." This prevents the system from churning through sites you aren't currently using and should speed up the forecasting.
* **SIMBAD Import Update:** I adjusted the SIMBAD import code to handle a recent renaming of their data columns (`ra(d)` to `ra`). This suppresses a deprecation warning and ensures we keep fetching object data without interruption.
* **Manual Coordinate Checks:** Added an additional validation check when manually entering Right Ascension (RA) and Declination (DEC) values. This helps catch potential typos or format errors before they get saved to your database.

### Bug Fixes

* **Mini Graph Shading:** Fixed a visual bug where the grey night-time shading on the mini altitude graphs wasn't rendering correctly. The charts should look accurate again now.

---

Thanks again for all your patience and feedback, it really keeps this project going. Happy imaging!