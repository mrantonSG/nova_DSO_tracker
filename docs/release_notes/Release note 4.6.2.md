
### Nova DSO Tracker v4.6.2 Release Notes

Hi everyone! You might be surprised to see another update so soon after yesterday's release. I found a couple of critical bugs that slipped through the cracks, so I wanted to get a fix out to you immediately rather than making you wait.

This release includes everything from v4.6.1 plus these urgent fixes.

**Critical Hotfixes**

* **Project Page Stability:** Fixed a crash that occurred on the project page if a session had zero integration time recorded. The page should now load reliably regardless of your session data.
* **Project Data Saving:** Resolved an issue where Goals and Status settings in projects were not being saved or displayed. Your project planning data will now persist correctly.

**New Features (from v4.6.1)**

* **Geostationary Satellite Belt:** Added a toggle to display the Geostationary Satellite Belt in the Framing Assistant. This should help you frame your shots to avoid those unwanted satellite trails.
* **Exact Source Search:** You can now use the "=" operator to limit search results to a single specific source. This small tweak should make finding exact objects much faster.
* **Swift Interface Support:** I have added the initial groundwork to support a future Swift frontend. It is still early, but this prepares the app for some exciting interface updates in the future.
* **New Loading Graphic:** Added a new graphic to display while the app is processing data. Hopefully, this makes the wait for your calculations feel a little shorter.

**Bug Fixes (from v4.6.1)**

* **Horizon Mask Altitude:** Fixed a bug where the horizon mask defaulted to 20 degrees instead of your custom settings. Your local horizon limits should now display accurately on the charts.
* **Journal Photo Alignment:** Adjusted the layout for journal entries to prevent large photos from becoming misaligned. Your images should now sit strictly within the entry borders.
* **Keyboard Input in Journal:** Squashed a bug that blocked certain keys in the journal if the framing modal had been opened previously. You can now type your notes without interruption.
* **Stuck Calculation Message:** Resolved a glitch where the "calculating" status would remain on screen after data loaded. The interface now clears properly once the work is done.
* **Inspiration Data Import:** Fixed an issue that stopped default inspiration data from importing for new users. Everyone should now see the starter content immediately.
* **ASIAIR Coordinates:** Corrected a coordinate transfer error when sending plans to ASIAIR. Your target coordinates will now transfer perfectly for mosaics and framing.

Thanks again for all your patience and feedback, it really keeps this project going. Clear skies!
