# skyglow — Directional Sky Brightness from VIIRS

Computes a **skyglow horizon**: for each azimuth direction, the altitude
angle at which artificial sky brightness crosses an acceptable threshold
(configurable, defaults to 2/3 of zenith SQM). This is the core data
needed to overlay a skyglow shading band on Nova's DSO altitude graph.

## Dependencies

Add to Nova's `requirements.txt`:
```
rasterio>=1.3
h5py>=3.0
```

Install into the Nova venv:
```bash
source venv/bin/activate
pip install rasterio h5py
```

## Setup

1. Register at https://urs.earthdata.nasa.gov (free)
2. Generate a token at Profile → Generate Token
3. Store it in a `.env` file or pass via `--token`:

```bash
# .env (already gitignored in Nova)
NASA_EARTHDATA_TOKEN=eyJ...
```

## Usage

```bash
source venv/bin/activate

# Basic — uses token from .env
python -m tools.skyglow --lat 47.828 --lon 16.170 --elev 281

# Explicit token
python -m tools.skyglow --lat 47.828 --lon 16.170 --elev 281 --token eyJ...

# Custom threshold and sectors
python -m tools.skyglow --lat 47.828 --lon 16.170 --elev 281 \
    --sqm-threshold 20.5 --sectors 36

# Force re-download even if tile is cached
python -m tools.skyglow --lat 47.828 --lon 16.170 --elev 281 --refresh
```

## Cache

VIIRS tiles (HDF5, ~150MB each, 10°×10°) are cached in:
```
tools/skyglow/cache/VNP46A4_h19v04_2024.h5
```

Tiles are shared across locations — Bad Fischau and Vienna both use
`h19v04`. Re-download once per year when NASA releases new annual data
(typically May of the following year).

## Output

- `skyglow_<lat>_<lon>_polar.png` — polar plot for visual validation
- `skyglow_<lat>_<lon>.json` — skyglow horizon profile (future Nova payload)

## JSON format

```json
{
  "lat": 47.828, "lon": 16.170, "elev_m": 281,
  "viirs_year": 2024,
  "sqm_zenith": 20.75,
  "sqm_horizon_mean": 19.8,
  "threshold_sqm": 20.5,
  "threshold_source": "user",
  "skyglow_horizon": [
    {"az_deg": 0.0,  "az_label": "N",   "min_alt_deg": 24.0, "sqm_at_horizon": 19.4},
    {"az_deg": 22.5, "az_label": "NNE", "min_alt_deg": 31.0, "sqm_at_horizon": 18.9},
    ...
  ]
}
```

`min_alt_deg` is the altitude above which SQM exceeds the threshold —
the key value for Nova's graph overlay.

## Physics notes

The altitude-dependent sky brightness uses the Garstang (1986) simplified
scattering model: for each (azimuth, altitude) point on the sky hemisphere,
contributions from surrounding VIIRS pixels are weighted by
`radiance * exp(-k * d) / d²`, where the effective distance `d` accounts
for the slant path through the atmosphere at the given elevation angle.

Observer elevation above sea level shifts the base of the atmospheric
column, reducing skyglow contributions slightly for elevated sites.

The absolute SQM scale is calibrated so that the total (all-sky) Garstang
sum matches the known zenith SQM derived from the same VIIRS data using
the lightpollutionmap.info conversion formula.

This is an approximation — not full radiative transfer — but validated
against the lightpollutionmap.info all-sky view for Bad Fischau and
produces directional patterns consistent with the reference.
