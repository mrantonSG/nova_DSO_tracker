### Nova DSO Tracker v4.6.1 Release Notes

Hey everyone! I’m excited to share a minor maintenance update, v4.6.1. This release focuses on squashing a few bugs you've reported and adding some handy new tools to make your planning smoother.

**New Features**

* **Geostationary Satellite Belt:** You can now toggle a display of the Geostationary Satellite Belt directly in the Framing Assistant. This should help you avoid those pesky trails in your long exposures!
* **Exact Source Search:** I've added a small but useful tweak to the object search. Using the "=" operator now limits your search results to a single specific source, helping you find exactly what you need faster.
* **Swift Interface Support:** I've added the initial interface groundwork to support a future Swift frontend. It’s still early days, but this lays the foundation for some exciting potential updates down the road.
* **New Loading Graphic:** You'll notice a new graphic when the app is crunching numbers. Hopefully, it makes the brief wait for your data a little more pleasant.

**Bug Fixes**

* **Horizon Mask Altitude:** Fixed a bug where the horizon mask would default to drawing at 20 degrees instead of your custom altitude limit. Your local horizon should now display correctly on the charts.
* **Journal Photo Alignment:** Large photos in the journal entries were sometimes misaligned. I’ve adjusted the layout so your images should now sit nicely within the entry.
* **Keyboard Input in Journal:** Fixed an annoying issue where certain letters were blocked when typing in the journal if you had previously opened the framing modal. You should be able to type freely again!
* **Stuck Calculation Message:** Resolved a glitch where the "calculating" message would sometimes get stuck on the screen even after the data had loaded.
* **Inspiration Data Import:** Fixed an issue preventing inspiration data from being automatically imported for new users. Everyone should now see the default inspiration content right from the start.
* **ASIAIR Coordinates:** Corrected a coordinate transfer issue when sending plans to ASIAIR. Your mosaics and framing targets should now transfer accurately.

Thanks again for all your patience and feedback, it really keeps this project going. Clear skies!