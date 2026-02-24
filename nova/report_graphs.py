"""
nova/report_graphs.py - Generate charts for session/project reports.

Creates static PNG charts from parsed log data for embedding in HTML reports.
Uses matplotlib with a print-friendly style matching the Nova design system.
"""

import io
import base64
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for server-side rendering
import matplotlib.pyplot as plt
from matplotlib.figure import Figure


# =============================================================================
# STYLE CONFIGURATION - Nova Design System Colors
# =============================================================================

# Primary palette (matching CSS variables)
# Note: Use hex colors or (r,g,b) tuples for matplotlib - NOT CSS rgba() strings
COLORS = {
    'primary': '#83b4c5',           # Nova teal
    'primary_light': '#a8cdd8',
    'primary_fill': (0.51, 0.71, 0.77, 0.3),      # Teal with alpha for fill
    'secondary': '#e88a8a',         # Rose for Dec
    'secondary_fill': (0.91, 0.54, 0.54, 0.3),    # Rose with alpha for fill
    'total': '#9b7ed9',             # Purple for total
    'success': '#6dcab0',           # Green
    'warning': '#f39c12',           # Amber
    'danger': '#e06060',            # Red
    'text': '#333333',
    'text_secondary': '#666666',
    'grid': '#e5e5e5',
    'bg': '#ffffff',
}

# AF V-curve color cycle (8 colors matching JS implementation)
AF_COLORS = [
    '#4a90d9',  # Blue
    '#e88a8a',  # Rose
    '#6dcab0',  # Green
    '#f39c12',  # Amber
    '#9b7ed9',  # Purple
    '#83b4c5',  # Teal
    '#e06060',  # Red
    '#8bc34a',  # Light green
]


def _create_figure(width_inches: float = 6.0, height_inches: float = 3.0) -> Tuple[Figure, plt.Axes]:
    """Create a figure with Nova styling."""
    fig, ax = plt.subplots(figsize=(width_inches, height_inches), facecolor=COLORS['bg'])
    ax.set_facecolor(COLORS['bg'])

    # Style the axes
    ax.tick_params(colors=COLORS['text_secondary'], labelsize=8)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['bottom'].set_color(COLORS['grid'])
    ax.spines['left'].set_color(COLORS['grid'])
    ax.grid(True, linestyle='-', linewidth=0.5, color=COLORS['grid'], alpha=0.5)

    return fig, ax


def _fig_to_base64(fig: Figure, dpi: int = 150) -> Optional[str]:
    """Convert a matplotlib figure to a base64 PNG string."""
    try:
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight',
                    facecolor=fig.get_facecolor(), edgecolor='none')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        return img_base64
    except Exception as e:
        plt.close(fig)
        return None


def _fit_parabola(x: np.ndarray, y: np.ndarray) -> Optional[Tuple[np.ndarray, float, float, float]]:
    """
    Fit a parabola to the data points.

    Returns:
        Tuple of (y_fitted, vertex_x, vertex_y, a_coefficient) or None if fitting fails
    """
    try:
        # Filter out NaN/inf values
        mask = np.isfinite(x) & np.isfinite(y)
        x_clean = x[mask]
        y_clean = y[mask]

        if len(x_clean) < 3:
            return None

        # Fit quadratic: y = ax^2 + bx + c
        coeffs = np.polyfit(x_clean, y_clean, 2)
        a, b, c = coeffs

        # Only accept parabolas that open upward (a > 0 for minimum)
        if a <= 0:
            return None

        # Calculate vertex
        vertex_x = -b / (2 * a)
        vertex_y = a * vertex_x**2 + b * vertex_x + c

        # Generate fitted curve
        x_fit = np.linspace(x_clean.min(), x_clean.max(), 100)
        y_fit = np.polyval(coeffs, x_fit)

        return y_fit, vertex_x, vertex_y, a

    except Exception:
        return None


# =============================================================================
# CHART GENERATORS
# =============================================================================

def generate_guiding_rms_chart(phd2_data: Dict[str, Any]) -> Optional[str]:
    """
    Generate a guiding RMS over time chart from PHD2 data.

    Args:
        phd2_data: Parsed PHD2 log data containing 'rms' array

    Returns:
        Base64 PNG string or None if no data
    """
    if not phd2_data or not phd2_data.get('rms'):
        return None

    rms_data = phd2_data['rms']
    if len(rms_data) < 2:
        return None

    # Extract data: [h, ra_rms_as, dec_rms_as, total_rms_as]
    hours = np.array([r[0] for r in rms_data])
    ra_rms = np.array([r[1] for r in rms_data])
    dec_rms = np.array([r[2] for r in rms_data])
    total_rms = np.array([r[3] for r in rms_data])

    fig, ax = _create_figure(6.5, 3.0)

    # Plot RA RMS with fill
    ax.plot(hours, ra_rms, color=COLORS['primary'], linewidth=1.5, label='RA RMS')
    ax.fill_between(hours, 0, ra_rms, color=COLORS['primary_fill'], alpha=0.3)

    # Plot Dec RMS with fill
    ax.plot(hours, dec_rms, color=COLORS['secondary'], linewidth=1.5, label='Dec RMS')
    ax.fill_between(hours, 0, dec_rms, color=COLORS['secondary_fill'], alpha=0.3)

    # Plot Total RMS as dashed line
    ax.plot(hours, total_rms, color=COLORS['total'], linewidth=1.0, linestyle='--', label='Total RMS')

    # Labels and styling
    ax.set_xlabel('Time (hours)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_ylabel('RMS (arcsec)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_title('Guiding Performance', fontsize=11, color=COLORS['text'], fontweight='600', pad=10)

    # Set Y-axis limit based on data
    max_rms = max(total_rms.max() * 1.5, 1.0)
    ax.set_ylim(0, min(max_rms, 5.0))  # Cap at 5" for readability

    ax.legend(loc='upper right', fontsize=8, framealpha=0.9)

    return _fig_to_base64(fig)


def generate_guiding_scatter_chart(phd2_data: Dict[str, Any]) -> Optional[str]:
    """
    Generate a guide pulse scatter plot showing RA vs Dec corrections.

    Args:
        phd2_data: Parsed PHD2 log data containing 'frames' array

    Returns:
        Base64 PNG string or None if no data
    """
    if not phd2_data or not phd2_data.get('frames'):
        return None

    frames = phd2_data['frames']
    if len(frames) < 10:
        return None

    # Extract RA and Dec guide distances (columns 4 and 5 in frames)
    # frames format: [h, ra_px, dec_px, snr, ra_guide_dist, dec_guide_dist, ...]
    ra_corr = np.array([f[4] if len(f) > 4 and f[4] is not None else 0 for f in frames])
    dec_corr = np.array([f[5] if len(f) > 5 and f[5] is not None else 0 for f in frames])

    # Filter out zero values
    mask = (ra_corr != 0) | (dec_corr != 0)
    if mask.sum() < 10:
        return None

    ra_corr = ra_corr[mask]
    dec_corr = dec_corr[mask]

    fig, ax = _create_figure(4.0, 4.0)

    # Plot scatter
    ax.scatter(ra_corr, dec_corr, c=COLORS['primary'], alpha=0.5, s=8, edgecolors='none')

    # Add crosshair at origin
    ax.axhline(y=0, color=COLORS['grid'], linewidth=0.5, linestyle='-')
    ax.axvline(x=0, color=COLORS['grid'], linewidth=0.5, linestyle='-')

    # Equal axis scaling
    max_val = max(np.abs(ra_corr).max(), np.abs(dec_corr).max(), 0.5)
    ax.set_xlim(-max_val * 1.1, max_val * 1.1)
    ax.set_ylim(-max_val * 1.1, max_val * 1.1)
    ax.set_aspect('equal')

    # Labels
    ax.set_xlabel('RA Correction (px)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_ylabel('Dec Correction (px)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_title('Guide Pulse Distribution', fontsize=11, color=COLORS['text'], fontweight='600', pad=10)

    return _fig_to_base64(fig)


def generate_dither_settle_chart(asiair_data: Dict[str, Any]) -> Optional[str]:
    """
    Generate a dither settle time bar chart from ASIAIR data.

    Args:
        asiair_data: Parsed ASIAIR log data containing 'dithers' array

    Returns:
        Base64 PNG string or None if no data
    """
    if not asiair_data or not asiair_data.get('dithers'):
        return None

    dithers = asiair_data['dithers']
    if len(dithers) == 0:
        return None

    fig, ax = _create_figure(6.0, 2.5)

    # Extract data
    dither_nums = np.arange(1, len(dithers) + 1)
    settle_times = np.array([d.get('dur', 0) for d in dithers])
    success = np.array([d.get('ok', True) for d in dithers])

    # Color bars based on success
    colors = [COLORS['success'] if ok else COLORS['danger'] for ok in success]

    bars = ax.bar(dither_nums, settle_times, color=colors, edgecolor='white', linewidth=0.5, alpha=0.8)

    # Add settle threshold line (typical ~10s)
    ax.axhline(y=10, color=COLORS['warning'], linewidth=1, linestyle='--', alpha=0.7, label='Typical threshold')

    # Labels
    ax.set_xlabel('Dither #', fontsize=9, color=COLORS['text_secondary'])
    ax.set_ylabel('Settle Time (s)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_title('Dither Settle Times', fontsize=11, color=COLORS['text'], fontweight='600', pad=10)

    # Set x-axis to show integer dither numbers
    ax.set_xticks(dither_nums[::max(1, len(dither_nums)//10)])

    # Add legend
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor=COLORS['success'], label='Success'),
        Patch(facecolor=COLORS['danger'], label='Timeout'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=8, framealpha=0.9)

    return _fig_to_base64(fig)


def generate_autofocus_vcurve(asiair_data: Dict[str, Any], run_index: int = None) -> Optional[str]:
    """
    Generate an AutoFocus V-curve chart from ASIAIR AF run data.

    Args:
        asiair_data: Parsed ASIAIR log data containing 'af_runs' array
        run_index: Optional specific run index to plot (None = combined overlay)

    Returns:
        Base64 PNG string or None if no data
    """
    if not asiair_data or not asiair_data.get('af_runs'):
        return None

    af_runs = asiair_data['af_runs']

    # Filter to specific run if requested
    if run_index is not None:
        if run_index < 0 or run_index >= len(af_runs):
            return None
        af_runs = [af_runs[run_index]]

    # Check for valid data
    valid_runs = [r for r in af_runs if r.get('points') and len(r['points']) >= 3]
    if not valid_runs:
        return None

    fig, ax = _create_figure(6.0, 3.5)

    focus_positions = []

    for i, run in enumerate(valid_runs):
        points = run['points']
        positions = np.array([p['pos'] for p in points])
        sizes = np.array([p['sz'] for p in points])

        color = AF_COLORS[i % len(AF_COLORS)]

        # Plot raw data points
        ax.scatter(positions, sizes, c=color, s=30, alpha=0.8, edgecolors='white', linewidth=0.5,
                   label=f'Run {run.get("run", i+1)}')

        # Try to fit parabola
        fit_result = _fit_parabola(positions, sizes)
        if fit_result:
            y_fit, vertex_x, vertex_y, _ = fit_result
            x_fit = np.linspace(positions.min(), positions.max(), 100)
            ax.plot(x_fit, y_fit, color=color, linewidth=1.5, alpha=0.6)

            # Mark focus position
            if run.get('focus_pos'):
                focus_pos = run['focus_pos']
                focus_positions.append((focus_pos, run.get('run', i+1), color))
            else:
                focus_positions.append((vertex_x, run.get('run', i+1), color))

    # Add vertical lines at focus positions
    for focus_pos, run_num, color in focus_positions:
        ax.axvline(x=focus_pos, color=color, linewidth=1, linestyle='--', alpha=0.5)

    ax.set_xlabel('Focus Position (steps)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_ylabel('Star Size (HFR)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_title('AutoFocus V-Curves', fontsize=11, color=COLORS['text'], fontweight='600', pad=10)

    if len(valid_runs) <= 5:
        ax.legend(loc='upper right', fontsize=8, framealpha=0.9)

    return _fig_to_base64(fig)


def generate_autofocus_drift_chart(asiair_data: Dict[str, Any]) -> Optional[str]:
    """
    Generate a focus position drift chart over the session.

    Args:
        asiair_data: Parsed ASIAIR log data containing 'af_runs' array

    Returns:
        Base64 PNG string or None if no data
    """
    if not asiair_data or not asiair_data.get('af_runs'):
        return None

    af_runs = asiair_data['af_runs']

    # Filter runs with valid focus positions
    valid_runs = [r for r in af_runs if r.get('focus_pos') is not None]
    if len(valid_runs) < 2:
        return None

    fig, ax = _create_figure(5.0, 2.5)

    # Extract data
    hours = np.array([r.get('h', 0) for r in valid_runs])
    focus_positions = np.array([r['focus_pos'] for r in valid_runs])

    # Plot line with markers
    ax.plot(hours, focus_positions, color=COLORS['warning'], linewidth=1.5, marker='o',
            markersize=6, markeredgecolor='white', markeredgewidth=0.5)

    # Fill under curve
    ax.fill_between(hours, focus_positions.min(), focus_positions, color=COLORS['warning'], alpha=0.15)

    # Labels
    ax.set_xlabel('Time (hours)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_ylabel('Focus Position (steps)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_title('Focus Position Drift', fontsize=11, color=COLORS['text'], fontweight='600', pad=10)

    # Add annotation for total drift
    total_drift = focus_positions[-1] - focus_positions[0]
    drift_text = f'Drift: {total_drift:+d} steps'
    ax.annotate(drift_text, xy=(0.02, 0.98), xycoords='axes fraction',
                fontsize=8, color=COLORS['text_secondary'],
                verticalalignment='top')

    return _fig_to_base64(fig)


def generate_autocenter_chart(asiair_data: Dict[str, Any]) -> Optional[str]:
    """
    Generate an autocenter accuracy bar chart.

    Args:
        asiair_data: Parsed ASIAIR log data containing 'autocenters' array

    Returns:
        Base64 PNG string or None if no data
    """
    if not asiair_data or not asiair_data.get('autocenters'):
        return None

    autocenters = asiair_data['autocenters']
    if len(autocenters) == 0:
        return None

    fig, ax = _create_figure(5.0, 2.5)

    # Extract data
    attempt_nums = np.arange(1, len(autocenters) + 1)
    distances = np.array([ac.get('distance_pct', 0) for ac in autocenters])
    centered = np.array([ac.get('centered', False) for ac in autocenters])

    # Color bars based on success
    colors = [COLORS['success'] if c else COLORS['warning'] for c in centered]

    bars = ax.bar(attempt_nums, distances, color=colors, edgecolor='white', linewidth=0.5, alpha=0.8)

    # Labels
    ax.set_xlabel('Attempt #', fontsize=9, color=COLORS['text_secondary'])
    ax.set_ylabel('Distance from Center (%)', fontsize=9, color=COLORS['text_secondary'])
    ax.set_title('Autocenter Accuracy', fontsize=11, color=COLORS['text'], fontweight='600', pad=10)

    # Set x-axis to show integer attempt numbers
    ax.set_xticks(attempt_nums[::max(1, len(attempt_nums)//10)])

    return _fig_to_base64(fig)


# =============================================================================
# BATCH GENERATION HELPERS
# =============================================================================

def generate_session_charts(log_analysis: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Generate all available charts for a session's log analysis data.

    Args:
        log_analysis: Dict with 'has_logs', 'asiair', 'phd2' keys

    Returns:
        Dict with chart names as keys and base64 strings (or None) as values
    """
    charts = {
        'guiding_rms': None,
        'guiding_scatter': None,
        'dither_settle': None,
        'af_vcurve': None,
        'af_drift': None,
        'autocenter': None,
    }

    if not log_analysis or not log_analysis.get('has_logs'):
        return charts

    # PHD2 charts
    phd2 = log_analysis.get('phd2')
    if phd2:
        charts['guiding_rms'] = generate_guiding_rms_chart(phd2)
        charts['guiding_scatter'] = generate_guiding_scatter_chart(phd2)

    # ASIAIR charts
    asiair = log_analysis.get('asiair')
    if asiair:
        charts['dither_settle'] = generate_dither_settle_chart(asiair)
        charts['af_vcurve'] = generate_autofocus_vcurve(asiair)
        charts['af_drift'] = generate_autofocus_drift_chart(asiair)
        charts['autocenter'] = generate_autocenter_chart(asiair)

    return charts


def generate_individual_af_curves(asiair_data: Dict[str, Any]) -> List[Tuple[int, Optional[str]]]:
    """
    Generate individual V-curve charts for each AF run.

    Args:
        asiair_data: Parsed ASIAIR log data containing 'af_runs' array

    Returns:
        List of (run_number, base64_string) tuples
    """
    if not asiair_data or not asiair_data.get('af_runs'):
        return []

    results = []
    for i, run in enumerate(asiair_data['af_runs']):
        chart = generate_autofocus_vcurve(asiair_data, run_index=i)
        if chart:
            results.append((run.get('run', i+1), chart))

    return results
