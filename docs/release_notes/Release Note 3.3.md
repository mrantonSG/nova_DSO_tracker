
***

## Nova DSO Tracker v3.3.0 Released!

I'm excited to announce the release of Nova DSO Tracker v3.3.0! This version introduces powerful new planning tools, major quality-of-life enhancements, and important structural updates to make the application more robust and easier to manage.

---

### Highlights

#### Major Overhaul of the Framing Assistant
The Framing Assistant has been completely revamped to be a more powerful and intuitive planning tool.

* **New Rotation Controls**: The rotation slider now operates from **-90° to +90°**, making it easier to adjust framing from a central starting point.
* **Lock to FOV**: A new **"Lock FOV"** checkbox keeps the Field of View rectangle centered on your screen as you pan and zoom across the sky, making it incredibly easy to explore the surrounding region while keeping your target in context.
* **Image Blending & Tuning**: You can now **blend a second sky survey** over the base image with an opacity slider.


#### Advanced Rig Sorting & Management
You now have full control over how your equipment is displayed on the **Rigs configuration page**.

* A new **sorting dropdown** allows you to organize your rigs by:
    * Name (A-Z, Z-A)
    * Effective Focal Length
    * f/ratio
    * Image Scale
    * Field of View Width
    * Date Added
* Your sorting preference is **automatically saved** and remembered for your next session.

####  Improved Responsiveness & Scaling
The user interface, especially the **Framing Assistant modal** and the **main altitude chart**, now scales more intelligently to fit different screen sizes. This provides a much-improved experience on laptops and a wider range of monitor resolutions.

---

### Under the Hood

#### New File Structure
To make your data easier to manage and back up, all user-specific files have been moved into a dedicated `instance/` directory within the application folder.

* **Configuration Files**: All `config_*.yaml`, `journal_*.yaml`, and `rigs_*.yaml` files are now located in `instance/configs/`.
* **Cache Files**: All temporary cache data is now stored in `instance/cache/`.
* **Backups**: When you import a new file, the old one is safely backed up into `instance/backups/`.

This change means your core data is no longer mixed with the application's source code, making future updates and backups much cleaner.

#### Anonymous Telemetry for Better Development
This version introduces an optional and anonymous telemetry system to help me understand how the app is used and where to focus development efforts.

* **What it is for**: It sends a small, anonymous "heartbeat" to help me understand things like which operating systems are most common and if it runs under Docker.
* **What it collects**: It only sends **anonymous aggregate data**, such as your app version, OS type (e.g., Windows, Linux), and the *counts* of your objects, rigs, and locations. (to understand if we run in a bottleneck)
* **What it DOES NOT collect**: It **NEVER** sends any personal data, including the names of your objects, your location coordinates, your project notes, or any other sensitive information.
* **It is Opt-Out**: You can disable this feature at any time on the **Configuration -> General** page.

Your privacy is paramount, and this system was designed with that as the highest priority. Thank you for considering leaving it enabled to help improve Nova!

---

Thank you for your continued support. We hope these new features enhance your astrophotography planning!

Happy imaging!