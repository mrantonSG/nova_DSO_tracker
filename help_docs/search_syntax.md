#### Main Dashboard

This is your mission control. The main dashboard gives you a real-time overview of your target library, calculated for your current location and time. It is designed to answer the question: *"What is best to image right now?"*

**Visibility Note**
By default, objects that are geometrically impossible to see from your current location (i.e., they never rise above your configured horizon threshold) are **hidden** to keep the list clean. These objects will reappear instantly if you explicitly search for them by name or ID.

**Data Columns**

* **Altitude/Azimuth:** Current real-time position.
* **11 PM:** Position at 11 PM tonight, helping you plan for the core imaging hours.
* **Trend:** Shows if the object is rising (↑) or setting (↓).
* **Max Altitude:** The highest point the object reaches tonight.
* **Observable Time:** Total minutes the object is above your configured horizon limit.

**Advanced Filtering**

The filter row below the headers is powerful. You can use special operators to refine your list:

* **Text Search:** Type normally to find matches (e.g., `M31`, `Nebula`). Note that searching for a specific object will override the "hidden invisible objects" setting.
* **Numeric Comparisons:**
* `>50`: Matches values greater than 50.
* `<20`: Matches values less than 20.
* `>=` / `<=`: Greater/Less than or equal to.
* **Ranges (AND Logic):** Combine operators to find values within a specific window.
* Example: `>140 <300` in the *Azimuth* column finds objects currently in the southern sky (between 140° and 300°).
* **Exclusion (NOT Logic):** Start with `!` to exclude items.
* Example: `!Galaxy` in the *Type* column hides all galaxies.
* Example: `!Cyg` in *Constellation* hides targets in Cygnus.
* **Multiple Terms (OR Logic):** Separate terms with commas.
* Example: `M31, M33, M42` in *Object* shows only those three targets.
* Example: `Nebula, Cluster` in *Type* shows both nebulae and clusters.

**Saved Views**

Once you create a useful filter set (e.g., "Galaxies High in South"), click the **Save** button next to the "Saved Views" dropdown. You can name this view and instantly recall it later.

**Visual Discovery**

The **Inspiration** tab offers a graphical way to browse potential targets. Instead of a data table, it presents:

* **Smart Suggestions:** The app automatically highlights "Top Picks"—objects that are currently well-positioned (high altitude) and have a long observable duration for the night.
* **Visual Cards:** Each target is displayed as a card with an image, description, and key stats (Max Altitude, Duration) at a glance.
* **Interactive Details:** Click any card to view full details or jump directly to its charts.

**Tabs**

* **Position:** Real-time coordinates and visibility.
* **Properties:** Static data like Magnitude, Size, and Constellation.
* **Outlook:** A long-term forecast showing the best nights to image your active projects.
* **Heatmap:** A visual yearly calendar showing when objects are visible.
* **Inspiration:** A visual gallery of currently visible targets with images and summaries.
* **Journal:** A quick-access list of all your recorded sessions.