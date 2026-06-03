# Understanding SQM Zenith

**SQM** (Sky Quality Meter) measures how dark your sky is at the zenith, in units of **mag/arcsec²**. Higher values mean darker skies — a truly dark site reads around 21.5–22, while a suburban sky might be 19–20.

## Bortle vs. SQM

If you leave SQM Zenith blank, Nova derives a nominal SQM value from your **Bortle scale** setting using a standard mapping. This is a reasonable estimate for most planning purposes.

If you have an actual SQM reading from a meter or an app like *Clear Outside* or *Sky Quality Meter*, enter it here. Nova will use your measured value instead of the Bortle estimate — this gives more accurate limiting magnitude calculations and AI ranker scoring.

## Typical Values by Bortle Class

| Bortle | Sky type | Typical SQM |
|--------|----------|-------------|
| 1 | Truly dark | ≥ 21.9 |
| 3 | Rural | ~21.5 |
| 5 | Suburban | ~20.4 |
| 7 | Suburban/urban | ~19.1 |
| 9 | Inner city | ≤ 18.0 |

## Tips

- A single reading on a clear, moonless night at zenith is sufficient.
- SQM varies with humidity, smoke, and seasonal airglow — don't worry about micro-precision. A value within 0.2 mag of reality is plenty accurate.
- If you image from multiple locations, each location can have its own SQM value.
