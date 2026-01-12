
## Nova DSO Tracker v4.4.0

I am really excited about this update. I've been trying to make the planning tools more practical for actual imaging sessions, especially for those of you using specific gear like the ASIAIR. I also tried to make the app feel a bit snappier—or at least be more honest when it's thinking!

### New Stuff

**Mosaics & Framing (The Big Feature)**
I finally added Mosaic mode to the Framing Assistant! You can now define a grid (Columns x Rows) and set your overlap percentage directly in the framing modal.
* The framing tool now visualizes the mosaic grid overlay on the sky survey.
* I added a button to copy the plan as a CSV format specifically compatible with the ASIAIR (Plan Import) or N.I.N.A.
* This works on both the main desktop app and the mobile companion.

**Performance & Progress Bars**
The app was getting a little heavy when loading large lists, so I tried to improve the user experience there.
* **Up Now (Mobile):** I added a progress bar here so you aren't staring at a blank screen while it does the math. I also implemented a caching mechanism that holds this data for 5 minutes to save your battery and my server CPU.
* **Main Dashboard:** The index page now also sports a progress bar and a 60-second cache to keep things feeling quicker when you are clicking around.

**Mobile Improvements**
* I changed the load order so the "Add Object" function loads first in the mobile app. Hopefully, this makes adding targets on the fly feel a bit more responsive.

### Fixes

* **New Projects Not Showing:** I squashed a bug where newly created projects weren't showing up immediately in the list. You should see them right away now.
* **Default Location Logic:** I overhauled how the default location is set and saved. It should be much more reliable now and handle automatic switching better if you have multiple setups.

### A Quick Note
I'm just one person working on this in my spare time, so if you find any quirks with the new mosaic export or the caching, please be patient with me! I really appreciate everyone who uses Nova and helps me make it better.

Clear skies!