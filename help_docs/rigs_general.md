
#### Rigs Configuration

The **Rigs** tab is where you define your imaging equipment. While it might seem like simple data entry, setting this up is **critical** for unlocking the full power of the Nova App.

**Why Rigs Are Important**

* **Framing & Mosaics:** The visual Framing Tool relies entirely on your Rig definitions to draw accurate sensor rectangles. **Without a saved Rig, the Framing Tool and Mosaic Planner will not work.**
* **Journal Reports:** Your observation logs link directly to these Rigs. Defining them here ensures that your future Journal Reports automatically include detailed technical specs (like focal length and pixel scale) without you having to type them in every time.

**1. Define Your Components**

Before you can build a full rig, you must define the individual pieces of gear in your inventory.

* **Telescopes:** Enter the Aperture and Focal Length (in mm).
* **Cameras:** Enter the Sensor Dimensions (mm) and Pixel Size (microns). This data is essential for calculating your field of view.
* **Reducers / Extenders:** Enter the optical factor (e.g., `0.7` for a reducer, `2.0` for a Barlow).

**2. Configure Your Rigs**

Once your components are added, combine them into a functional imaging system.

* **Create Rig:** Give your setup a nickname (e.g., "Redcat Widefield") and select the specific Telescope, Camera, and optional Reducer from the dropdowns.
* **Automatic Stats:** Nova instantly calculates your **Effective Focal Length**, **F-Ratio**, and **Image Scale** (arcsec/pixel).

**Sampling Analysis**

Use the **"Select Your Typical Seeing"** dropdown to check your optical performance. Nova will analyze your Image Scale against local sky conditions and tell you if your setup is **Undersampled**, **Oversampled**, or a perfect match.
