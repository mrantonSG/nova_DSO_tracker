
# Understanding the Horizon Mask

The **Horizon Mask** tells Nova exactly where the physical obstructions are in your specific location. It uses a list of coordinate points to draw a "skyline" that blocks out parts of the sky.

Each point in the list is a pair of numbers: `[Azimuth, Altitude]`.

  * **Azimuth (0-360):** The compass direction. 0 is North, 90 is East, 180 is South, etc.
  * **Altitude (0-90):** How high the obstruction is in degrees at that direction.

## Seeing it in Action

To give you a better idea, I took the data from my own garden pier (where I battle a house and some tall trees) and visualized it.

![Horizon Mask Example](/api/help/img/Horizonmask.jpeg)

In this graph:

  * The **Brown Area** is the blocked sky defined by the coordinates.
  * The **Red Dashed Line** is the global Altitude Threshold (more on that below).
  * The **Blue Area** is your actual free imaging zone.

## How to Write Your Mask

The data is entered as a simple list of coordinate pairs. You don't need to be a programmer to do this, just follow the pattern\!

**The Data Format:**

```text
[[Azimuth, Altitude], [Azimuth, Altitude], ...]
```

**My Garden Example:**
Here is the raw data used to generate the graphic above. You can copy this structure and change the numbers to match your sky:

```text
[[0.0, 0.0], [30.0, 30.0], [60.0, 36.0], [80.0, 25.0], [83.0, 30.0], [85.0, 20.0], 
[88.0, 0.0], [120.0, 30.0], [130.0, 20.0], [132.0, 0.0]]
```

### Key Rules for a Good Mask

1.  **Points Connect Automatically:** Nova draws a straight line between every point you list. If you define a point at `[88, 0]` and the next one at `[120, 30]`, it creates a slope connecting them.
2.  **Use "0" to Break Obstructions:** Because the points connect, you need to bring the altitude back down to `0.0` to "end" an obstruction.
      * *Notice in the example:* I end the first big block at `[88.0, 0.0]` and then start the next peak.
3.  **You Don't Need the Whole 360:** You don't have to start at 0 or end at 360. If you only have one big tree between Azimuth 140 and 160, you just need to add points for that specific area. The rest of the sky will remain clear by default.

## Importing from Stellarium

If you use Stellarium and have a `.hzn` or `.txt` horizon file, you can import it directly instead of typing the data by hand.

1. Click the **Import .hzn** button below the Horizon Mask text area.
2. Select your Stellarium horizon file (`.hzn` or `.txt`).
3. The file is parsed automatically and the Horizon Mask field is populated with the converted data.

Comment lines (starting with `#` or `;`) are ignored. If the file contains more than 100 data points, it is automatically simplified to keep the data lightweight. Values are rounded to one decimal place and sorted by azimuth.

## The "Net Observable Time"

You might notice a setting in your config called **Altitude Threshold** (the default is default 20 degrees - you can set it under "General").

  * **Altitude Threshold:** This is the global minimum height an object must reach to be considered good for imaging (to avoid thick atmosphere/muck near the horizon).
  * **Horizon Mask:** This cuts out specific chunks of sky *above* that threshold.

Nova combines these two smarts. It calculates the **Net Observable Time**—meaning it only counts time where the object is above your global 20° limit **AND** not hidden behind the specific shapes in your Horizon Mask.
