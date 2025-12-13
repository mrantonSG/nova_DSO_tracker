
#### Find & Merge Duplicates

Over time, your library might accumulate duplicate entries for the same target (e.g., you imported "M 42" manually, but also added "NGC 1976" from a catalog). This tool scans your database to find and resolve these conflicts.

**How it Works**

The Nova App scans your entire object library to find pairs of objects that are located within **2.5 arcminutes** of each other.

**Resolving Duplicates**

When duplicates are found, they are displayed side-by-side. You must decide which version is the "Master" (the one you keep) and which is the duplicate to be merged and removed.

* **Keep A, Merge B:** Object A stays. Object B is deleted, but its data is moved to A.
* **Keep B, Merge A:** Object B stays. Object A is deleted, but its data is moved to B.

**What Happens to My Data?**

Merging is a **smart process** designed to preserve your history. When you merge an object:

* **Journals:** All imaging sessions linked to the deleted object are re-linked to the "Kept" object.
* **Projects:** Any active or past projects are moved to the "Kept" object.
* **Framings:** Saved framing data is moved. (Note: If *both* objects had saved framing, the "Kept" object's framing is preserved).
* **Notes:** Private notes are not lost! Notes from the deleted object are appended to the bottom of the "Kept" object's notes.

**Tip:** It is usually best to keep the object with the most common name (e.g., Keep "M 31", Merge "Andromeda Galaxy") to make future searching easier.
