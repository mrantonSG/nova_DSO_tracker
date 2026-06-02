"""
tools/skyglow/__main__.py

CLI entry point for the skyglow pilot.
Run as: python -m tools.skyglow [options]

See tools/skyglow/README.md for setup and usage.
"""

import argparse
import json
import os
import sys
from pathlib import Path


def get_token(args_token: str | None) -> str:
    """Resolve NASA Earthdata token from CLI arg or environment."""
    token = args_token or os.environ.get("NASA_EARTHDATA_TOKEN", "")
    if not token:
        env_path = Path.cwd() / "instance" / ".env"
        if not env_path.exists():
            env_path = Path(__file__).parent.parent / "instance" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("NASA_EARTHDATA_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not token:
        print("ERROR: NASA Earthdata token not found.")
        print("  Set NASA_EARTHDATA_TOKEN in your instance/.env file, or pass --token.")
        print("  Get a token at: https://urs.earthdata.nasa.gov/profile")
        sys.exit(1)
    return token


def main():
    ap = argparse.ArgumentParser(
        description="Compute directional skyglow profile from VIIRS data."
    )
    ap.add_argument("--lat",    type=float, default=47.828,
                    help="Latitude (default: Bad Fischau, AT)")
    ap.add_argument("--lon",    type=float, default=16.170,
                    help="Longitude")
    ap.add_argument("--elev",   type=float, default=281,
                    help="Observer elevation in metres (default: 281)")
    ap.add_argument("--radius", type=float, default=150,
                    help="Integration radius in km (default: 150)")
    ap.add_argument("--sectors", type=int,  default=16,
                    help="Azimuth sectors (default: 16)")
    ap.add_argument("--year",   type=int,   default=2024,
                    help="VIIRS year (default: 2024)")
    ap.add_argument("--k",      type=float, default=0.35,
                    help="Garstang extinction coefficient (default: 0.35)")
    ap.add_argument("--sqm-threshold", type=float, default=None,
                    dest="sqm_threshold",
                    help="SQM threshold for skyglow horizon (default: auto 2/3)")
    ap.add_argument("--sqm-zenith", type=float, default=None,
                    dest="sqm_zenith",
                    help=(
                        "Zenith SQM for this location (required for accurate results). "
                        "Look up at lightpollutionmap.info or derive from Bortle: "
                        "B1=21.7 B2=21.5 B3=21.3 B4=20.8 B5=20.3 B6=19.5 B7=18.5 B8=17.5 B9=17.0. "
                        "Default: 20.3 (Bortle 5)."
                    ))
    ap.add_argument("--token",  type=str,   default=None,
                    help="NASA Earthdata token (or set NASA_EARTHDATA_TOKEN in .env)")
    ap.add_argument("--refresh", action="store_true",
                    help="Force re-download even if tile is cached")
    ap.add_argument("--output", type=str,   default=None,
                    help="Output prefix (default: skyglow_<lat>_<lon>)")
    ap.add_argument("--no-plot", action="store_true",
                    help="Skip plot generation")
    args = ap.parse_args()

    token  = get_token(args.token)
    prefix = args.output or f"skyglow_{args.lat:.3f}_{args.lon:.3f}"

    print(f"\n=== Nova Skyglow Pilot ===")
    print(f"Location : {args.lat:.4f}°N, {args.lon:.4f}°E  ({args.elev:.0f}m)")
    print(f"Radius   : {args.radius} km  |  Sectors: {args.sectors}")
    print(f"VIIRS    : {args.year}  |  k: {args.k}")
    if args.sqm_threshold:
        print(f"Threshold: {args.sqm_threshold:.2f} mag/arcsec² (user-set)")
    else:
        print(f"Threshold: auto (2/3 of zenith–horizon range)")

    # ── 1. Tile cache ──────────────────────────────────────────────────────
    from tools.skyglow.cache import (
        tiles_for_radius, is_cached, download_tile, load_tile_data, cache_path
    )

    print(f"\n[1/4] Resolving VIIRS tile(s)...")
    tiles = tiles_for_radius(args.lat, args.lon, args.radius)
    print(f"  Tiles needed: {['h{:02d}v{:02d}'.format(h,v) for h,v in tiles]}")

    # For now pilot handles single-tile case (150km radius fits in one tile
    # for most European locations). Multi-tile mosaic is a future enhancement.
    if len(tiles) > 1:
        print(f"  Note: {len(tiles)} tiles span this radius. "
              f"Using primary tile only for pilot.")
    h, v = tiles[0]

    if args.refresh and is_cached(h, v, args.year):
        cache_path(h, v, args.year).unlink()
        print(f"  Cache cleared (--refresh)")

    print(f"\n[2/4] Loading VIIRS data (h{h:02d}v{v:02d} {args.year})...")
    download_tile(h, v, args.year, token, progress=True)
    data, lats_g, lons_g = load_tile_data(
        h, v, args.year, args.lat, args.lon, args.radius
    )
    print(f"  Window: {data.shape[1]}×{data.shape[0]} px, "
          f"{int((data > 0).sum()):,} lit pixels")

    # ── 3. Garstang model ─────────────────────────────────────────────────
    print(f"\n[3/4] Computing skyglow profile...")
    from tools.skyglow.garstang import (
        compute_skyglow_profile, compute_skyglow_horizon, sqm_to_bortle
    )

    profile = compute_skyglow_profile(
        lat_obs=args.lat, lon_obs=args.lon, elev_obs_m=args.elev,
        data=data, lats_g=lats_g, lons_g=lons_g,
        radius_km=args.radius, n_sectors=args.sectors,
        k=args.k, sqm_zenith=args.sqm_zenith,
    )

    profile = compute_skyglow_horizon(
        profile,
        threshold_sqm=args.sqm_threshold,
        threshold_source="user" if args.sqm_threshold else "auto_2/3",
    )

    # Add metadata for Nova integration
    profile["lat"]       = args.lat
    profile["lon"]       = args.lon
    profile["elev_m"]    = args.elev
    profile["viirs_year"] = args.year
    profile["radius_km"] = args.radius
    profile["garstang_k"] = args.k
    profile["bortle_zenith"] = sqm_to_bortle(profile["sqm_zenith"])

    # ── Print summary table ────────────────────────────────────────────────
    alt_steps = profile["alt_steps"]
    horizon   = profile["skyglow_horizon"]

    print(f"\n  {'Az':>6}  {'Dir':3}  "
          + "  ".join(f"{a:>5}°" for a in alt_steps)
          + "  MinAlt")
    print(f"  {'──':>6}  {'───':3}  "
          + "  ".join("──────" for _ in alt_steps)
          + "  ──────")

    for sec, hor in zip(profile["sectors"], horizon):
        sqm_row = "  ".join(f"{s:>5.2f}" for s in sec["sqm_by_alt"])
        print(f"  {sec['az_deg']:5.1f}°  {sec['az_label']:3}  {sqm_row}"
              f"  {hor['min_alt_deg']:>5.1f}°")

    print(f"\n  Zenith SQM   : {profile['sqm_zenith']:.2f}  "
          f"(Bortle {profile['bortle_zenith']})")
    print(f"  Horizon mean : {profile['sqm_horizon_mean']:.2f}")
    print(f"  Threshold    : {profile['threshold_sqm']:.2f}  "
          f"({profile['threshold_source']})")

    min_alt_vals = [h["min_alt_deg"] for h in horizon]
    best_dir  = horizon[min_alt_vals.index(min(min_alt_vals))]["az_label"]
    worst_dir = horizon[min_alt_vals.index(max(min_alt_vals))]["az_label"]
    print(f"  Best dir     : {best_dir} ({min(min_alt_vals):.1f}° min alt)")
    print(f"  Worst dir    : {worst_dir} ({max(min_alt_vals):.1f}° min alt)")

    # ── 4. Outputs ─────────────────────────────────────────────────────────
    print(f"\n[4/4] Writing outputs...")

    json_path = f"{prefix}.json"
    with open(json_path, "w") as f:
        json.dump(profile, f, indent=2)
    print(f"  Saved: {json_path}")

    if not args.no_plot:
        from tools.skyglow.plot import plot_polar, plot_horizon
        p1 = plot_polar(profile, args.lat, args.lon, args.year, prefix)
        p2 = plot_horizon(profile, args.lat, args.lon, prefix)
        print(f"  Saved: {p1}")
        if p2:
            print(f"  Saved: {p2}")

    print(f"\nDone.")


if __name__ == "__main__":
    main()