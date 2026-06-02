"""
tools/skyglow/cache.py

VIIRS tile download and local cache management.
Tiles are HDF5 files (~150MB each, 10x10 degree grid) from NASA LAADS DAAC.
A tile is shared by all locations that fall within its bounding box.
"""

import json
import math
import os
import ssl
import certifi
import urllib.request
import urllib.error
from pathlib import Path

# macOS python.org installer: system certs are not available by default.
# Use certifi (already a dependency) to provide CA bundle for SSL verification.
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# Cache directory relative to this file
CACHE_DIR = Path(__file__).parent / "cache"

# NASA endpoints
CMR_URL = (
    "https://cmr.earthdata.nasa.gov/search/granules.json"
    "?short_name=VNP46A4&provider=LAADS"
    "&bounding_box={lon_min},{lat_min},{lon_max},{lat_max}"
    "&temporal={year}-01-01T00:00:00Z,{year}-01-02T00:00:00Z"
    "&page_size=10"
)
DOWNLOAD_BASE = "https://data.laadsdaac.earthdatacloud.nasa.gov/prod-lads/VNP46A4"

DATASET_PATH = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/AllAngle_Composite_Snow_Free"
LAT_PATH     = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/lat"
LON_PATH     = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/lon"


def tile_for_location(lat: float, lon: float) -> tuple[int, int]:
    """
    Return (h, v) tile indices for a lat/lon.
    VNP46A4 uses a simple geographic 10x10 degree grid:
      h = floor((lon + 180) / 10)
      v = floor((90 - lat) / 10)
    """
    h = int((lon + 180) / 10)
    v = int((90 - lat) / 10)
    return h, v


def tiles_for_radius(lat: float, lon: float, radius_km: float = 150) -> list[tuple[int, int]]:
    """
    Return all unique tile (h, v) pairs that intersect a circle of
    radius_km around lat/lon. For most European locations this is just
    one tile; occasionally two at tile boundaries.
    """
    R = 6371.0
    dlat = math.degrees(radius_km / R)
    dlon = math.degrees(radius_km / (R * math.cos(math.radians(lat))))

    tiles = set()
    for la in [lat - dlat, lat, lat + dlat]:
        for lo in [lon - dlon, lon, lon + dlon]:
            tiles.add(tile_for_location(la, lo))
    return list(tiles)


def cache_path(h: int, v: int, year: int) -> Path:
    return CACHE_DIR / f"VNP46A4_h{h:02d}v{v:02d}_{year}.h5"


def is_cached(h: int, v: int, year: int) -> bool:
    p = cache_path(h, v, year)
    return p.exists() and p.stat().st_size > 1_000_000  # > 1MB = real file


def resolve_filename(h: int, v: int, year: int) -> str | None:
    """
    Query NASA CMR to get the exact filename (which contains a production
    timestamp we can't predict). Returns the filename stem, e.g.
    'VNP46A4.A2024001.h19v04.002.2025162032851.h5', or None on failure.
    """
    # Tile bounding box
    lon_min = h * 10 - 180
    lat_max = 90 - v * 10
    lon_max = lon_min + 10
    lat_min = lat_max - 10

    url = CMR_URL.format(
        lon_min=lon_min, lat_min=lat_min,
        lon_max=lon_max, lat_max=lat_max,
        year=year
    )

    try:
        with urllib.request.urlopen(url, timeout=30, context=_SSL_CTX) as r:
            data = json.loads(r.read())
    except Exception as e:
        raise RuntimeError(f"CMR search failed: {e}")

    entries = data.get("feed", {}).get("entry", [])
    tile_id = f"h{h:02d}v{v:02d}"

    for entry in entries:
        for link in entry.get("links", []):
            href = link.get("href", "")
            if tile_id in href and href.endswith(".h5") and "prod-lads" in href:
                return href.split("/")[-1]

    return None


def download_tile(h: int, v: int, year: int, token: str,
                  progress: bool = True) -> Path:
    """
    Download a VIIRS tile from NASA Earthdata Cloud.
    Returns path to the cached HDF5 file.
    Skips download if already cached.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = cache_path(h, v, year)

    if is_cached(h, v, year):
        if progress:
            print(f"  Cache hit: {dest.name}")
        return dest

    print(f"  Resolving filename for h{h:02d}v{v:02d} {year} via CMR...")
    filename = resolve_filename(h, v, year)
    if filename is None:
        raise RuntimeError(
            f"Could not find VNP46A4 tile h{h:02d}v{v:02d} for {year} in NASA CMR. "
            f"The tile may not exist (ocean-only) or the year is not yet available."
        )

    url = f"{DOWNLOAD_BASE}/{filename}"
    print(f"  Downloading {filename} (~150MB)...")

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})

    try:
        with urllib.request.urlopen(req, timeout=600, context=_SSL_CTX) as response:
            # Follow redirect to presigned S3 URL (no auth header needed there)
            final_url = response.geturl()
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk = 1024 * 1024  # 1MB chunks

            with open(dest, "wb") as f:
                while True:
                    buf = response.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    downloaded += len(buf)
                    if progress and total:
                        pct = downloaded / total * 100
                        mb = downloaded / 1048576
                        print(f"  {mb:.0f} / {total/1048576:.0f} MB ({pct:.0f}%)\r",
                              end="", flush=True)

        if progress:
            print(f"\n  Saved: {dest}")

    except Exception as e:
        # Clean up partial download
        if dest.exists():
            dest.unlink()
        raise RuntimeError(f"Download failed: {e}")

    return dest


def load_tile_data(h: int, v: int, year: int,
                   lat: float, lon: float, radius_km: float):
    """
    Load the radiance window from a cached HDF5 tile.
    Returns (data_2d, lats_grid, lons_grid) cropped to the bounding box.
    data_2d values are in nW/cm²/sr.
    """
    import h5py
    import numpy as np

    path = cache_path(h, v, year)
    if not is_cached(h, v, year):
        raise RuntimeError(f"Tile {path.name} not in cache — call download_tile first.")

    R = 6371.0
    dlat = math.degrees(radius_km / R)
    dlon = math.degrees(radius_km / (R * math.cos(math.radians(lat))))
    lat_min, lat_max = lat - dlat, lat + dlat
    lon_min, lon_max = lon - dlon, lon + dlon

    with h5py.File(path, "r") as f:
        lats_1d = f[LAT_PATH][:]   # shape (2400,)
        lons_1d = f[LON_PATH][:]   # shape (2400,)
        data_full = f[DATASET_PATH][:]  # shape (2400, 2400)

    # Crop to bounding box
    row_mask = (lats_1d >= lat_min) & (lats_1d <= lat_max)
    col_mask = (lons_1d >= lon_min) & (lons_1d <= lon_max)
    rows = np.where(row_mask)[0]
    cols = np.where(col_mask)[0]

    if len(rows) == 0 or len(cols) == 0:
        raise RuntimeError(
            f"Location ({lat}, {lon}) with radius {radius_km}km "
            f"does not overlap tile h{h:02d}v{v:02d}."
        )

    r0, r1 = rows[0], rows[-1] + 1
    c0, c1 = cols[0], cols[-1] + 1

    data_crop = data_full[r0:r1, c0:c1].astype(np.float32)
    lats_crop = lats_1d[r0:r1]
    lons_crop = lons_1d[c0:c1]

    # Replace fill values with 0
    data_crop = np.where(data_crop < 0, 0, data_crop)

    # Build 2D coordinate grids
    lons_g, lats_g = np.meshgrid(lons_crop, lats_crop)

    return data_crop, lats_g, lons_g


def get_or_download_tile(lat: float, lon: float, year: int, token: str):
    """Download tile if not cached, then load and return (data, lats_grid, lons_grid)."""
    h, v = tile_for_location(lat, lon)
    if not is_cached(h, v, year):
        download_tile(h, v, year, token, progress=False)
    return load_tile_data(h, v, year, lat, lon, radius_km=150.0)
