
# DSO Altitude Graph

The graph shows a 24-hour window centered on local midnight, giving you a full picture of when your target is observable tonight.

## Chart Lines

**Object Altitude**: the blue line showing your target's altitude above the horizon throughout the day and night.

**Moon Altitude**: the yellow line showing the moon's position. When the moon is high and close to your target, expect increased sky background.

**Object Azimuth / Moon Azimuth**: dashed lines on the right axis showing the compass direction (0–360°) of each object. Useful for checking whether obstructions or light sources are in the way.

## Overlays

**Horizon Mask**: the grey shaded area represents your configured terrain mask. Any part of the object's altitude curve inside this area is blocked by local terrain or obstructions.

**Horizon**: the flat line at 0° marking the mathematical horizon.

**Skyglow Floor**: the amber shaded area shows the minimum altitude at which the sky background reaches your site's SQM threshold in each compass direction. When your object is above this floor, it is in relatively clean sky. When it dips below, light pollution from that direction becomes significant. The shape varies with azimuth: directions toward cities or towns will show a higher floor than dark directions. Configure your site's Bortle scale or SQM Zenith value in Locations settings for accurate results.

## Weather Bar

The coloured bar at the top shows forecast cloud cover and seeing conditions hour by hour. Hover over a segment for details.

## Sun Events

Vertical lines mark Sunset, Astronomical Dusk, Astronomical Dawn, and Sunrise. The best imaging window is typically between Astronomical Dusk and Astronomical Dawn.

## Date and View Controls

Use the Day/Month/Year controls at the bottom to navigate to a specific date. **Simulation Mode** (the clock icon in the header) lets you preview any night as if it were tonight.

