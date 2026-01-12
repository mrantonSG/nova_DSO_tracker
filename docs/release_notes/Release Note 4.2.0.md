

# Nova DSO Tracker v4.2.0

This is a huge milestone release. The biggest change is that I’ve moved the core data storage from flat YAML files to a proper SQLite database structure. It was a massive change to the codebase, but it makes the app faster, safer, and ready for the future.

### New Stuff

* **The Big Database Migration:** I've moved framing, objects, and sessions into a robust database. This fixes that frustrating issue where the "Add Framing" button would disappear because the app lost track of the data.
* **Project Planning Mode:** There is now a dedicated **New Project** button. This makes it much easier to plan your imaging targets ahead of time rather than just reacting to what's up in the sky.
* **Shared Views (Filters):** You can now share your selection filters (Saved Views). If you have a specific way you like to sort or filter objects, you can save it and even share those settings.
* **Specific Filter Types:** I've improved the object type filtering to be more specific, helping you find exactly what you want to shoot.

###  Fixes (Pardon the Mess!)

* **Project Session Math:** I realized the calculation for total project integration time was a bit off. I've fixed the math so your session totals should actually make sense now.
* **Outlook Filtering:** Fixed a bug where your filters weren't applying to the Outlook (forecasting) tool. Now, if you filter for specific objects, the Outlook will correctly respect that.
* **Startup Stability:** For those running on multi-core systems, I added a "file lock" system to manage threading during startup. This prevents the app from tripping over itself when it first boots up.
* **Rig Snapshots:** When you save a session, the app now takes a "snapshot" of your rig's specs at that moment. This means if you change your telescope later, your old journal entries won't break!

###  Under the Hood

* **RA/DEC Import Tests:** I've added more rigorous test cases for importing coordinates to ensure accuracy.
* **Multi-User Logic:** A lot of work went into ensuring the new database structure handles single-user and multi-user modes correctly without crossing wires.

***

As always, thank you so much for using Nova. I'm just one developer trying to make the best free tool I can, and your patience with these big architectural changes means the world to me. Clear skies!

***
