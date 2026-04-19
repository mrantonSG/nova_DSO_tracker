# Calibration Star

Before guiding, PHD2 and ASIAIR need to **calibrate** the guide camera axes by moving the mount in RA and DEC and measuring the resulting star motion. For the most accurate calibration, your guide star should meet two criteria:

1. **Near the celestial equator**: Declination within **±20°** of 0°, where RA/DEC movements are most orthogonal
2. **Near the meridian**: Hour Angle within **±1.5 hours**, minimising mount cone-error and declination backlash effects

## How This Widget Helps

This widget finds the **best available bright star** that satisfies both criteria for your location and the selected date, then shows the time window during which that star is in the usable calibration zone.

## Usage

1. Set the **date** in the Chart tab to your planned imaging night
2. The widget displays the recommended calibration star with its **RA/Dec coordinates**
3. The **calibration window** shows when the star is within the optimal zone
4. In ASIAIR, **slew to the shown RA/Dec** before starting your guiding calibration
5. Complete calibration, then slew back to your imaging target

## Tips

- If no star is found, try selecting a different date or check that your location is set correctly
- The refresh button (↻) re-runs the star search for the current date
- Brighter stars (lower magnitude) are preferred for more reliable guide star detection
