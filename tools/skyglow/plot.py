"""
tools/skyglow/plot.py

Visualisation for the skyglow profile and horizon.
Produces two figures:
  1. Polar plot — sky brightness vs azimuth at multiple altitudes
  2. Horizon plot — the skyglow horizon contour (min acceptable altitude
     per azimuth), comparable to Nova's existing horizon mask display
"""

import math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.cm import ScalarMappable


def plot_polar(profile: dict, lat: float, lon: float,
               year: int, output_prefix: str) -> str:
    """
    Polar plot showing SQM at each altitude ring × azimuth sector.
    The innermost ring is the horizon (alt=0°), outermost is zenith (alt=90°).
    Colour: plasma colormap, yellow=dark sky, purple=light polluted.
    """
    try:
        cmap = matplotlib.colormaps["plasma"]
    except AttributeError:
        cmap = plt.cm.get_cmap("plasma")

    sectors   = profile["sectors"]
    alt_steps = profile["alt_steps"]
    n_sec     = len(sectors)
    n_alt     = len(alt_steps)
    sw        = 2 * math.pi / n_sec

    sqm_min, sqm_max = 17.0, 22.0

    fig = plt.figure(figsize=(9, 9), facecolor="#0d0d0d")
    ax  = fig.add_subplot(111, projection="polar", facecolor="#0d0d0d")

    # Radial axis: 0 (horizon) → 1 (zenith), mapped as r = alt/90
    for s_idx, sector in enumerate(sectors):
        az_start = s_idx * sw
        az_end   = (s_idx + 1) * sw
        theta    = np.linspace(az_start, az_end, 20)

        for a_idx in range(n_alt - 1):
            r_inner = 1.0 - alt_steps[a_idx + 1] / 90.0
            r_outer = 1.0 - alt_steps[a_idx] / 90.0
            sqm_val = (sector["sqm_by_alt"][a_idx] +
                       sector["sqm_by_alt"][a_idx + 1]) / 2
            norm    = np.clip((sqm_val - sqm_min) / (sqm_max - sqm_min), 0, 1)
            color   = cmap(norm)

            r_in_arr  = np.full(20, r_inner)
            r_out_arr = np.full(20, r_outer)
            ax.fill_between(theta, r_in_arr, r_out_arr,
                            color=color, alpha=0.93, linewidth=0)

    # Draw skyglow horizon contour if present
    if "skyglow_horizon" in profile:
        horizon = profile["skyglow_horizon"]
        az_rad  = [math.radians(h["az_deg"] + (360 / n_sec / 2))
                   for h in horizon]
        r_vals  = [1.0 - h["min_alt_deg"] / 90.0 for h in horizon]
        # Close the loop
        az_rad.append(az_rad[0])
        r_vals.append(r_vals[0])
        ax.plot(az_rad, r_vals, color="white", linewidth=1.5,
                linestyle="--", alpha=0.8, label="Skyglow horizon")

    # Altitude rings
    for alt in [0, 30, 60, 90]:
        r = 1.0 - alt / 90.0
        ax.plot(np.linspace(0, 2 * math.pi, 360), [r] * 360,
                color="white", alpha=0.15, lw=0.7)
        if alt < 90:
            ax.text(math.radians(8), r + 0.02, f"{alt}°",
                    color="white", alpha=0.4, fontsize=7)

    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_xticks(np.radians([0, 90, 180, 270]))
    ax.set_xticklabels(["N", "E", "S", "W"],
                       color="white", fontsize=14, fontweight="bold")
    ax.set_yticks([])
    ax.set_ylim(0, 1.1)
    ax.spines["polar"].set_color("#ffffff30")

    sqm_z = profile["sqm_zenith"]
    sqm_h = profile["sqm_horizon_mean"]
    thr   = profile.get("threshold_sqm", "—")
    src   = profile.get("threshold_source", "")

    fig.suptitle(
        f"Skyglow Profile  —  VIIRS {year}\n"
        f"Lat {lat:.4f}°,  Lon {lon:.4f}°",
        color="white", fontsize=12, y=0.97
    )
    fig.text(
        0.5, 0.02,
        f"Zenith SQM: {sqm_z:.2f}  |  Horizon mean: {sqm_h:.2f}  |  "
        f"Threshold: {thr:.2f} ({src})  —  dashed line = skyglow horizon",
        color="#aaaaaa", fontsize=8, ha="center"
    )

    # Colourbar
    sm   = ScalarMappable(cmap=cmap,
                          norm=mcolors.Normalize(vmin=sqm_min, vmax=sqm_max))
    sm.set_array([])
    cax  = fig.add_axes([0.88, 0.15, 0.025, 0.65])
    cbar = fig.colorbar(sm, cax=cax)
    cbar.set_label("SQM (mag/arcsec²)", color="white", fontsize=9)
    cbar.ax.yaxis.set_tick_params(color="white")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white", fontsize=8)
    cbar.outline.set_edgecolor("#ffffff40")

    path = f"{output_prefix}_polar.png"
    plt.savefig(path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def plot_horizon(profile: dict, lat: float, lon: float,
                 output_prefix: str) -> str:
    """
    Linear azimuth plot of the skyglow horizon (min acceptable altitude
    per azimuth). Similar to how Nova displays the terrain horizon mask.
    X axis: azimuth 0–360°
    Y axis: altitude 0–90°
    Shaded area below the horizon = "avoid" zone
    """
    if "skyglow_horizon" not in profile:
        return None

    horizon = profile["skyglow_horizon"]
    az      = [h["az_deg"] for h in horizon] + [360.0]
    alt     = [h["min_alt_deg"] for h in horizon] + [horizon[0]["min_alt_deg"]]
    sqm_h   = [h["sqm_at_horizon"] for h in horizon] + [horizon[0]["sqm_at_horizon"]]

    fig, ax = plt.subplots(figsize=(12, 4), facecolor="#0d0d0d")
    ax.set_facecolor("#0d0d0d")

    # Shaded skyglow zone
    ax.fill_between(az, 0, alt, color="#cc4444", alpha=0.35,
                    label="Below threshold (avoid)")
    ax.plot(az, alt, color="#ff6666", linewidth=1.8,
            label=f"Skyglow horizon (SQM ≥ {profile['threshold_sqm']:.2f})")

    # Zenith SQM reference
    sqm_z = profile["sqm_zenith"]
    ax.axhline(y=0, color="#ffffff20", linewidth=0.5)

    # Cardinal labels
    for deg, label in [(0,"N"),(90,"E"),(180,"S"),(270,"W"),(360,"N")]:
        ax.axvline(x=deg, color="#ffffff15", linewidth=0.7)
        if deg < 360:
            ax.text(deg, 88, label, color="#aaaaaa", fontsize=9,
                    ha="center", va="top")

    ax.set_xlim(0, 360)
    ax.set_ylim(0, 90)
    ax.set_xlabel("Azimuth (°)", color="#aaaaaa")
    ax.set_ylabel("Min. altitude (°)", color="#aaaaaa")
    ax.tick_params(colors="#aaaaaa")
    for spine in ax.spines.values():
        spine.set_color("#444444")

    ax.set_title(
        f"Skyglow Horizon  —  Lat {lat:.4f}°, Lon {lon:.4f}°  |  "
        f"Zenith SQM {sqm_z:.2f}  |  Threshold {profile['threshold_sqm']:.2f}",
        color="white", fontsize=10
    )
    ax.legend(loc="upper right", facecolor="#1a1a1a",
              labelcolor="white", fontsize=8)

    path = f"{output_prefix}_horizon.png"
    plt.savefig(path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return path