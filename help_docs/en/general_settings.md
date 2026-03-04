
### General Settings

The **General** tab allows you to define the baseline rules the **Nova App** uses to calculate visibility and identify good imaging opportunities.

#### Visibility Basics
* **Altitude Threshold (°):** This is your "horizon floor." Objects below this angle (in degrees) are considered obstructed or too low to image. Setting this to 20° or 30° is standard to avoid atmospheric turbulence near the horizon.

#### Outlook & Imaging Criteria
These settings determine which targets appear in your "Outlook" forecast. The app uses these rules to filter out nights that don't meet your quality standards.

* **Min Observable (min):** The minimum amount of time an object must be visible above your threshold to be considered a valid opportunity.
* **Min Max Altitude (°):** The peak height an object must reach during the night. If an object never rises above this, Nova will skip it.
* **Max Moon Illum (%):** Use this to filter out nights where the moon is too bright. (e.g., set to 20% to only see dark-night opportunities).
* **Min Moon Sep (°):** The minimum distance allowed between your target and the Moon.
* **Search Months:** How far into the future the Outlook feature should calculate opportunities (default is 6 months).

#### System Performance
*(Note: These options are only available in Single-User Mode)*

* **Calculation Precision:** Controls how often Nova calculates an object's position to draw altitude curves.
    * **High (10 min):** Smoothest curves, but slower to load.
    * **Fast (30 min):** Faster loading times, ideal for low-power devices (like a Raspberry Pi).
* **Anonymous Telemetry:** If enabled, the **Nova App** sends a tiny, anonymous "heartbeat" containing basic system info (e.g., app version, object counts). No personal data is ever collected. This helps the developer understand how the app is used to improve future updates.
