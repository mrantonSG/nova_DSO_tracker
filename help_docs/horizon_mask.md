# Horizon Mask Help

The **Horizon Mask** allows Nova to ignore targets that are technically "up" in the sky but blocked by local obstructions like trees, buildings, or mountains.

## How to create one
The mask is defined as a list of points describing your skyline. Each point is a pair of numbers: `[Azimuth, Altitude]`.

* **Azimuth (0-360):** The compass direction (0=North, 90=East).
* **Altitude (0-90):** The height of the obstruction in degrees.

## Example Input
Copy and paste this list into the text box:
```text
[[0, 20], [90, 15], [180, 45], [270, 15]]