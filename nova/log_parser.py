"""
nova/log_parser.py - Parse ASIAIR and PHD2 logs for session analysis.

Returns structured data for Chart.js visualization matching the RAW structure
from the reference session_dashboard.jsx implementation.
"""
import re
import math
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from flask_babel import gettext as _


# --- LTTB Downsampling Algorithm ---

def lttb_downsample(points: List[List], threshold: int, x_idx: int = 0, y_idx: int = 1) -> List[List]:
    """
    Largest-Triangle-Three-Buckets (LTTB) downsampling algorithm.

    Preserves visual shape of time-series data while reducing point count.

    Args:
        points: List of points, each point is a list/tuple of values
        threshold: Target number of points (if len(points) <= threshold, returns unchanged)
        x_idx: Index of x-value in each point (default 0)
        y_idx: Index of y-value in each point for triangle area calculation (default 1)

    Returns:
        Downsampled list of points, preserving all original fields
    """
    n = len(points)
    if n <= threshold or n < 3:
        return points

    # Result list
    sampled = [points[0]]  # Always keep first point

    # Bucket size (divide remaining n-2 points into threshold-2 buckets)
    bucket_size = (n - 2) / (threshold - 2)

    a = 0  # Index of previously selected point

    for i in range(threshold - 2):
        # Calculate bucket range
        bucket_start = int((i + 1) * bucket_size) + 1
        bucket_end = int((i + 2) * bucket_size) + 1
        if bucket_end > n - 1:
            bucket_end = n - 1

        # Calculate average point of next bucket
        next_bucket_start = bucket_end
        next_bucket_end = int((i + 3) * bucket_size) + 1 if i + 3 <= threshold - 2 else n
        if next_bucket_end > n:
            next_bucket_end = n

        if next_bucket_start < next_bucket_end:
            avg_x = sum(points[j][x_idx] for j in range(next_bucket_start, next_bucket_end)) / (next_bucket_end - next_bucket_start)
            avg_y = sum(points[j][y_idx] for j in range(next_bucket_start, next_bucket_end)) / (next_bucket_end - next_bucket_start)
        else:
            avg_x = points[min(next_bucket_start, n - 1)][x_idx]
            avg_y = points[min(next_bucket_start, n - 1)][y_idx]

        # Find point in current bucket that maximizes triangle area
        max_area = -1
        max_idx = bucket_start

        px = points[a][x_idx]
        py = points[a][y_idx]

        for j in range(bucket_start, bucket_end):
            # Triangle area formula
            x = points[j][x_idx]
            y = points[j][y_idx]
            area = abs((px * (y - avg_y) + x * (avg_y - py) + avg_x * (py - y)) / 2)
            if area > max_area:
                max_area = area
                max_idx = j

        sampled.append(points[max_idx])
        a = max_idx

    sampled.append(points[-1])  # Always keep last point

    return sampled


# --- ASIAIR Log Patterns ---
ASIAIR_PATTERNS = {
    'timestamp': re.compile(r'^(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})'),
    'exposure': re.compile(r'Exposure\s+([\d.]+)\s*s.*image\s+(\d+)#', re.IGNORECASE),
    'exposure_ms': re.compile(r'Exposure\s+([\d.]+)\s*ms.*image\s+(\d+)#', re.IGNORECASE),
    'dither': re.compile(r'\[Guide\]\s+Dither\b'),
    'settle_start': re.compile(r'\[Guide\]\s+Dither\s+Settle'),
    'settle_done': re.compile(r'\[Guide\]\s+Settle\s+Done', re.IGNORECASE),
    'settle_timeout': re.compile(r'\[Guide\]\s+Settle\s+Timeout', re.IGNORECASE),
    'af_begin': re.compile(r'\[AutoFocus\|Begin\]', re.IGNORECASE),
    'af_end': re.compile(r'\[AutoFocus\|End\]', re.IGNORECASE),
    'af_star_size': re.compile(r'star\s+size\s+([\d.]+)', re.IGNORECASE),
    'af_position': re.compile(r'EAF\s+position\s+(\d+)', re.IGNORECASE),
    'af_focus_pos': re.compile(r'focused\s+position\s+is\s+(\d+)', re.IGNORECASE),
    'af_temp': re.compile(r'temperature\s+([-\d.]+)', re.IGNORECASE),
    'meridian_flip': re.compile(r'Meridian\s+Flip', re.IGNORECASE),
    'target_ra_dec': re.compile(r'Target\s+RA:([0-9hms]+)\s+DEC:([+-]?[0-9°\'\"]+)', re.IGNORECASE),
    'plate_solve': re.compile(r'Solve succeeded:\s*RA:([0-9hms]+)\s+DEC:([+-]?[0-9°\'\"]+)\s+Angle\s*=\s*([\d.]+)', re.IGNORECASE),
    'plate_solve_stars': re.compile(r'Star\s+number\s*=\s*(\d+)', re.IGNORECASE),
    'autocenter_distance': re.compile(r'distance\s*=\s*([\d.]+)%\s*\(([\d.]+)°?\)', re.IGNORECASE),
    'autocenter_success': re.compile(r'\[AutoCenter\|End\]\s*The target is centered', re.IGNORECASE),
    'autorun_begin': re.compile(r'\[Autorun\|Begin\]', re.IGNORECASE),
    'autorun_end': re.compile(r'\[Autorun\|End\]', re.IGNORECASE),
    'shooting_plan': re.compile(r'Shooting\s+(\d+)\s+\w+\s+frames?,\s*exposure\s+([\d.]+)(s|ms)\s*(?:Bin(\d+))?', re.IGNORECASE),
}


def parse_asiair_log(content: str) -> Dict[str, Any]:
    """
    Parse ASIAIR Autorun log content string.

    Returns RAW structure:
    {
        'session_start': str,  # ISO datetime string
        'target': {'ra': str, 'dec': str} or None,
        'shooting_plan': {'frames': int, 'exposure': float, 'bin': int} or None,
        'exposures': [{'h': float, 'img': int, 'dur': float}, ...],
        'dithers': [{'h': float, 'dur': float, 'ok': bool}, ...],
        'af_runs': [{
            'run': int,
            'ts': str,
            'h': float,
            'temp': Optional[float],
            'focus_pos': Optional[int],
            'points': [{'pos': int, 'sz': float}, ...]
        }, ...],
        'meridian_flips': [{'h': float, 'ts': str}, ...],
        'plate_solves': [{'h': float, 'ts': str, 'ra': str, 'dec': str, 'angle': float, 'stars': int}, ...],
        'autocenters': [{'h': float, 'ts': str, 'distance_pct': float, 'distance_deg': float, 'centered': bool}, ...],
        'stats': {
            'total_exposures': int,
            'total_exposure_time_sec': float,
            'total_time_min': float,
            'af_count': int,
            'dither_count': int,
            'dither_timeout_count': int,
            'meridian_flip_count': int,
            'plate_solve_count': int
        }
    }
    """
    result = _empty_asiair_result()

    if not content or not content.strip():
        return result

    lines = content.splitlines()
    session_start = None

    # Track state for multi-line parsing
    current_af = None
    pending_dither_start = None
    pending_dither_h = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Extract timestamp
        ts_match = ASIAIR_PATTERNS['timestamp'].match(line)
        if not ts_match:
            continue

        dt_str = ts_match.group(1)
        try:
            dt = datetime.strptime(dt_str, '%Y/%m/%d %H:%M:%S')
        except ValueError:
            continue

        # Initialize session start on first valid timestamp
        if session_start is None:
            session_start = dt

        # Calculate hours since session start
        hours_elapsed = (dt - session_start).total_seconds() / 3600.0

        # --- Exposure Detection ---
        exp_match = ASIAIR_PATTERNS['exposure'].search(line)
        if exp_match:
            duration = float(exp_match.group(1))
            img_num = int(exp_match.group(2))
            result['exposures'].append({
                'h': round(hours_elapsed, 4),
                'img': img_num,
                'dur': duration
            })
            result['stats']['total_exposures'] += 1
            result['stats']['total_exposure_time_sec'] += duration
            continue

        # --- Exposure Detection (ms format for flats) ---
        exp_ms_match = ASIAIR_PATTERNS['exposure_ms'].search(line)
        if exp_ms_match:
            duration_ms = float(exp_ms_match.group(1))
            img_num = int(exp_ms_match.group(2))
            result['exposures'].append({
                'h': round(hours_elapsed, 4),
                'img': img_num,
                'dur': duration_ms / 1000.0  # Convert to seconds
            })
            result['stats']['total_exposures'] += 1
            result['stats']['total_exposure_time_sec'] += duration_ms / 1000.0
            continue

        # --- Target RA/Dec ---
        target_match = ASIAIR_PATTERNS['target_ra_dec'].search(line)
        if target_match:
            result['target'] = {
                'ra': target_match.group(1),
                'dec': target_match.group(2)
            }
            continue

        # --- Shooting Plan ---
        shooting_match = ASIAIR_PATTERNS['shooting_plan'].search(line)
        if shooting_match:
            result['shooting_plan'] = {
                'frames': int(shooting_match.group(1)),
                'exposure': float(shooting_match.group(2)),
                'exposure_unit': shooting_match.group(3),
                'bin': int(shooting_match.group(4)) if shooting_match.group(4) else 1
            }
            continue

        # --- Plate Solve Results ---
        solve_match = ASIAIR_PATTERNS['plate_solve'].search(line)
        if solve_match:
            stars_match = ASIAIR_PATTERNS['plate_solve_stars'].search(line)
            result['plate_solves'].append({
                'h': round(hours_elapsed, 4),
                'ts': dt.isoformat(),
                'ra': solve_match.group(1),
                'dec': solve_match.group(2),
                'angle': float(solve_match.group(3)),
                'stars': int(stars_match.group(1)) if stars_match else 0
            })
            result['stats']['plate_solve_count'] += 1
            continue

        # --- AutoCenter Results ---
        # Case 1: "Too far from center, distance = X%"
        autocenter_match = ASIAIR_PATTERNS['autocenter_distance'].search(line)
        if autocenter_match:
            result['autocenters'].append({
                'h': round(hours_elapsed, 4),
                'ts': dt.isoformat(),
                'distance_pct': float(autocenter_match.group(1)),
                'distance_deg': float(autocenter_match.group(2)),
                'centered': False
            })
            continue

        # Case 2: "The target is centered" (no distance in log)
        if ASIAIR_PATTERNS['autocenter_success'].search(line):
            result['autocenters'].append({
                'h': round(hours_elapsed, 4),
                'ts': dt.isoformat(),
                'distance_pct': 0.0,
                'distance_deg': 0.0,
                'centered': True
            })
            continue

        # --- Dither Events ---
        if ASIAIR_PATTERNS['dither'].search(line) and not ASIAIR_PATTERNS['settle_start'].search(line):
            # Start of a dither cycle
            pending_dither_start = dt
            pending_dither_h = hours_elapsed
            result['stats']['dither_count'] += 1
            continue

        if ASIAIR_PATTERNS['settle_start'].search(line):
            # Mark that we're in a settle phase
            if pending_dither_start is None:
                pending_dither_start = dt
                pending_dither_h = hours_elapsed
            continue

        if ASIAIR_PATTERNS['settle_done'].search(line):
            if pending_dither_start is not None:
                settle_dur = (dt - pending_dither_start).total_seconds()
                result['dithers'].append({
                    'h': round(pending_dither_h, 4),
                    'dur': round(settle_dur, 1),
                    'ok': True
                })
                pending_dither_start = None
                pending_dither_h = None
            continue

        if ASIAIR_PATTERNS['settle_timeout'].search(line):
            if pending_dither_start is not None:
                settle_dur = (dt - pending_dither_start).total_seconds()
                result['dithers'].append({
                    'h': round(pending_dither_h, 4),
                    'dur': round(settle_dur, 1),
                    'ok': False
                })
                result['stats']['dither_timeout_count'] += 1
                pending_dither_start = None
                pending_dither_h = None
            continue

        # --- AutoFocus Events ---
        if ASIAIR_PATTERNS['af_begin'].search(line):
            # Close previous AF run if exists
            if current_af is not None:
                result['af_runs'].append(current_af)

            current_af = {
                'run': len(result['af_runs']) + 1,
                'ts': dt.isoformat(),
                'h': round(hours_elapsed, 4),
                'temp': None,
                'focus_pos': None,
                'points': []
            }
            result['stats']['af_count'] += 1

            # Extract temperature if present
            temp_match = ASIAIR_PATTERNS['af_temp'].search(line)
            if temp_match:
                current_af['temp'] = float(temp_match.group(1))
            continue

        if current_af is not None:
            # Look for V-curve points (star size + position)
            sz_match = ASIAIR_PATTERNS['af_star_size'].search(line)
            pos_match = ASIAIR_PATTERNS['af_position'].search(line)

            if sz_match and pos_match:
                current_af['points'].append({
                    'pos': int(pos_match.group(1)),
                    'sz': float(sz_match.group(1))
                })
                continue

            # Look for temperature during AF
            temp_match = ASIAIR_PATTERNS['af_temp'].search(line)
            if temp_match and current_af['temp'] is None:
                current_af['temp'] = float(temp_match.group(1))
                continue

        if ASIAIR_PATTERNS['af_end'].search(line):
            # Look for final focus position
            focus_match = ASIAIR_PATTERNS['af_focus_pos'].search(line)
            if focus_match and current_af is not None:
                current_af['focus_pos'] = int(focus_match.group(1))

            # Close current AF run
            if current_af is not None:
                result['af_runs'].append(current_af)
                current_af = None
            continue

        # --- Meridian Flip ---
        if ASIAIR_PATTERNS['meridian_flip'].search(line):
            result['meridian_flips'].append({
                'h': round(hours_elapsed, 4),
                'ts': dt.isoformat()
            })
            result['stats']['meridian_flip_count'] += 1
            continue

    # Close any pending AF run
    if current_af is not None:
        result['af_runs'].append(current_af)

    # Calculate total session time
    if result['exposures']:
        last_exp = result['exposures'][-1]
        result['stats']['total_time_min'] = round(last_exp['h'] * 60, 1)

    # --- Precision reduction: Round float values ---
    for exp in result['exposures']:
        exp['h'] = round(exp['h'], 4)
        exp['dur'] = round(exp['dur'], 4)
    for d in result['dithers']:
        d['h'] = round(d['h'], 4)
        d['dur'] = round(d['dur'], 4)
    for mf in result['meridian_flips']:
        mf['h'] = round(mf['h'], 4)
    for ps in result['plate_solves']:
        ps['h'] = round(ps['h'], 4)
        ps['angle'] = round(ps['angle'], 4)
    for ac in result['autocenters']:
        ac['h'] = round(ac['h'], 4)
        ac['distance_pct'] = round(ac['distance_pct'], 4)
        ac['distance_deg'] = round(ac['distance_deg'], 4)
    for af in result['af_runs']:
        af['h'] = round(af['h'], 4)
        if af['temp'] is not None:
            af['temp'] = round(af['temp'], 2)
        for pt in af.get('points', []):
            pt['sz'] = round(pt['sz'], 4)

    # Store session start time for clock time display
    if session_start:
        result['session_start'] = session_start.isoformat()

    return result


def parse_phd2_log(content: str) -> Dict[str, Any]:
    """
    Parse PHD2 Guide Log content string.

    Returns RAW structure:
    {
        'pixel_scale': float,  # arcsec/px
        'frames': [[h, ra_px, dec_px, snr, ra_guide_dist, dec_guide_dist, ra_dir, dec_dir, ra_dur, dec_dur], ...],
        'rms': [[h, ra_rms_as, dec_rms_as, total_rms_as], ...],  # rolling 30-frame RMS
        'settle': [{'h': float, 'dur': float, 'ok': bool}, ...],
        'run_bounds': [{'run': int, 'h': float, 'type': str}, ...],
        'stats': {
            'ra_rms_as': float,
            'dec_rms_as': float,
            'total_rms_as': float,
            'ra_rms_px': float,
            'dec_rms_px': float,
            'total_rms_px': float,
            'total_frames': int,
            'dither_count': int,
            'settle_success_count': int,
            'settle_timeout_count': int
        }
    }
    """
    result = _empty_phd2_result()

    if not content or not content.strip():
        return result

    lines = content.splitlines()

    # Track guiding sessions with absolute timestamps
    sessions = []
    current_session = None
    col_map = {}
    first_session_start = None  # Absolute start time of first session

    # Track settle windows for RMS exclusion
    pending_settle_start_h = None  # hours offset when settle started
    settle_windows = []  # [{h_start, h_end}, ...]

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # --- Header Parsing ---
        if "Pixel scale" in line:
            match = re.search(r'Pixel scale\s*=\s*([\d.]+)', line)
            if match:
                result['pixel_scale'] = float(match.group(1))
            continue

        if "Guiding Begins" in line:
            match = re.search(r'Guiding Begins\s+(?:at\s+)?(.+)$', line)
            ts_str = match.group(1).strip() if match else None
            try:
                dt = _parse_phd2_timestamp(ts_str) if ts_str else None
            except:
                dt = None

            # Track the first session's start time for relative hours
            if first_session_start is None and dt:
                first_session_start = dt

            # Calculate hours offset from first session
            hours_offset = 0
            if dt and first_session_start:
                hours_offset = (dt - first_session_start).total_seconds() / 3600.0

            # Start new session
            current_session = {
                'frames': [],  # [(time_sec, ra_px, dec_px, snr), ...]
                'start_dt': dt,
                'hours_offset': hours_offset
            }
            result['run_bounds'].append({
                'run': len(sessions) + 1,
                'h': round(hours_offset, 4),
                'type': 'start'
            })
            continue

        if "Guiding Ends" in line:
            if current_session is not None and current_session['frames']:
                # Calculate end hours from last frame
                last_frame_time = current_session['frames'][-1][0]  # time_sec
                end_h = current_session['hours_offset'] + (last_frame_time / 3600.0)
                result['run_bounds'].append({
                    'run': len(sessions) + 1,
                    'h': round(end_h, 4),
                    'type': 'end'
                })
                sessions.append(current_session)
            current_session = None
            continue

        # --- Column Header ---
        if line.startswith('Frame') and 'Time' in line and ',' in line:
            cols = [c.strip().replace('"', '') for c in line.split(',')]
            col_map = {name: i for i, name in enumerate(cols)}
            continue

        # --- SETTLING STATE CHANGE ---
        if "SETTLING STATE CHANGE" in line.upper():
            lower_line = line.lower()
            # Calculate current hours from last frame time in session
            current_h = None
            if current_session is not None and current_session['frames']:
                current_h = current_session['hours_offset'] + (current_session['frames'][-1][0] / 3600.0)
            elif current_session is not None:
                # No frames yet - use session start offset
                current_h = current_session['hours_offset']

            if 'settling started' in lower_line or 'started' in lower_line:
                # Record settle start at current time
                if current_session is not None and current_h is not None:
                    pending_settle_start_h = current_h
            elif 'settling complete' in lower_line or 'complete' in lower_line:
                result['stats']['settle_success_count'] += 1
                # Close settle window
                if pending_settle_start_h is not None and current_h is not None:
                    settle_windows.append({'h_start': pending_settle_start_h, 'h_end': current_h})
                    pending_settle_start_h = None
            elif 'timeout' in lower_line or 'failed' in lower_line:
                result['stats']['settle_timeout_count'] += 1
                # Close settle window even on timeout
                if pending_settle_start_h is not None and current_h is not None:
                    settle_windows.append({'h_start': pending_settle_start_h, 'h_end': current_h})
                    pending_settle_start_h = None
            continue

        # --- DITHER marker ---
        if "DITHER" in line.upper():
            result['stats']['dither_count'] += 1
            continue

        # --- Data Rows ---
        if current_session is not None and line and line[0].isdigit() and ',' in line:
            if not col_map:
                continue

            parts = line.split(',')

            def get_col_float(candidates: List[str]) -> Optional[float]:
                for c in candidates:
                    if c in col_map and col_map[c] < len(parts):
                        try:
                            val = parts[col_map[c]].strip().replace('"', '')
                            if val:
                                return float(val)
                        except (ValueError, IndexError):
                            pass
                return None

            def get_col_str(candidates: List[str]) -> Optional[str]:
                for c in candidates:
                    if c in col_map and col_map[c] < len(parts):
                        val = parts[col_map[c]].strip().replace('"', '')
                        if val:
                            return val
                return None

            # Get the Time column (seconds since this session started)
            time_sec = get_col_float(['Time'])
            # Extract RA/Dec errors (in pixels)
            ra = get_col_float(['RAErr', 'RARawDistance', 'dx', 'RA'])
            dec = get_col_float(['DecErr', 'DECRawDistance', 'dy', 'Dec'])
            snr = get_col_float(['SNR', 'StarMass'])
            # Extract guide pulse data
            ra_guide_dist = get_col_float(['RAGuideDistance'])
            dec_guide_dist = get_col_float(['DECGuideDistance'])
            ra_dur = get_col_float(['RADuration'])
            dec_dur = get_col_float(['DECDuration'])
            ra_dir = get_col_str(['RADirection'])
            dec_dir = get_col_str(['DECDirection'])

            if ra is not None and dec is not None and time_sec is not None:
                current_session['frames'].append((
                    time_sec,
                    ra,
                    dec,
                    snr if snr else 0,
                    ra_guide_dist,
                    dec_guide_dist,
                    ra_dir,
                    dec_dir,
                    ra_dur,
                    dec_dur
                ))

    # Handle unterminated session
    if current_session is not None and current_session['frames']:
        sessions.append(current_session)

    if not sessions:
        return result

    # Combine all sessions with absolute hours
    all_frames = []  # [(hours, ra, dec, snr, ra_guide_dist, dec_guide_dist, ra_dir, dec_dir, ra_dur, dec_dur), ...]
    for session in sessions:
        hours_offset = session['hours_offset']
        for frame_data in session['frames']:
            time_sec = frame_data[0]
            ra = frame_data[1]
            dec = frame_data[2]
            snr = frame_data[3]
            ra_guide_dist = frame_data[4] if len(frame_data) > 4 else None
            dec_guide_dist = frame_data[5] if len(frame_data) > 5 else None
            ra_dir = frame_data[6] if len(frame_data) > 6 else None
            dec_dir = frame_data[7] if len(frame_data) > 7 else None
            ra_dur = frame_data[8] if len(frame_data) > 8 else None
            dec_dur = frame_data[9] if len(frame_data) > 9 else None
            hours = hours_offset + (time_sec / 3600.0)
            all_frames.append((
                round(hours, 4), ra, dec, snr,
                ra_guide_dist, dec_guide_dist, ra_dir, dec_dir, ra_dur, dec_dur
            ))

    # Sort by hours
    all_frames.sort(key=lambda x: x[0])

    if not all_frames:
        return result

    # Output frames with all fields (backward compatible - first 4 fields are the same)
    result['frames'] = [[f[0], f[1], f[2], f[3],
                         f[4], f[5], f[6], f[7], f[8], f[9]] for f in all_frames]

    # Compute rolling RMS (30-frame window)
    window = 30
    ps = result['pixel_scale']

    for i in range(window, len(all_frames)):
        window_frames = all_frames[i - window:i]
        ra_vals = [f[1] for f in window_frames]
        dec_vals = [f[2] for f in window_frames]

        ra_rms_px = math.sqrt(sum(r * r for r in ra_vals) / len(ra_vals))
        dec_rms_px = math.sqrt(sum(d * d for d in dec_vals) / len(dec_vals))
        total_rms_px = math.sqrt(sum(r * r + d * d for r, d in zip(ra_vals, dec_vals)) / len(ra_vals))

        hours = all_frames[i][0]
        result['rms'].append([
            hours,
            round(ra_rms_px * ps, 3),
            round(dec_rms_px * ps, 3),
            round(total_rms_px * ps, 3)
        ])

    # --- Outlier filtering using IQR method ---
    def filter_outliers_iqr(values: List[float]) -> List[float]:
        """
        Remove outliers using IQR method (3x IQR upper bound).
        Only removes genuine extreme outliers, not normal guiding variations.
        """
        if len(values) < 4:
            return values
        sorted_v = sorted(values)
        q1 = sorted_v[len(sorted_v) // 4]
        q3 = sorted_v[3 * len(sorted_v) // 4]
        iqr = q3 - q1
        upper = q3 + 3.0 * iqr  # 3x IQR is conservative
        return [v for v in values if abs(v) <= upper]

    # Compute overall stats with outlier filtering
    all_ra = [f[1] for f in all_frames]
    all_dec = [f[2] for f in all_frames]

    all_ra_clean = filter_outliers_iqr(all_ra)
    all_dec_clean = filter_outliers_iqr(all_dec)
    outliers_removed = len(all_ra) - len(all_ra_clean) + len(all_dec) - len(all_dec_clean)

    result['stats']['ra_rms_px'] = round(math.sqrt(sum(r * r for r in all_ra_clean) / len(all_ra_clean)), 3) if all_ra_clean else 0
    result['stats']['dec_rms_px'] = round(math.sqrt(sum(d * d for d in all_dec_clean) / len(all_dec_clean)), 3) if all_dec_clean else 0
    result['stats']['total_rms_px'] = round(math.sqrt(result['stats']['ra_rms_px']**2 + result['stats']['dec_rms_px']**2), 3)

    result['stats']['ra_rms_as'] = round(result['stats']['ra_rms_px'] * ps, 3)
    result['stats']['dec_rms_as'] = round(result['stats']['dec_rms_px'] * ps, 3)
    result['stats']['total_rms_as'] = round(result['stats']['total_rms_px'] * ps, 3)
    result['stats']['total_frames'] = len(all_frames)
    result['stats']['outliers_removed'] = outliers_removed

    # --- Store settle windows for reference ---
    result['settle_windows'] = settle_windows

    # --- Validate pixel_scale before imaging stats calculation ---
    if ps is None or ps == 0:
        ps = 1.0  # Fallback to avoid division issues
        result['pixel_scale'] = ps

    # --- Calculate imaging-only RMS (excluding dither/settle periods) ---
    def is_during_settle(h: float, windows: List[Dict], buffer_h: float = 0.0083) -> bool:
        """
        Check if hour h falls within any settle window.
        buffer_h: pre-dither buffer in hours (0.0083h = 0.5min default)
        """
        for w in windows:
            # Apply buffer before h_start to catch the dither motion itself
            if (w['h_start'] - buffer_h) <= h <= w['h_end']:
                return True
        return False

    if settle_windows:
        # Filter frames that are NOT during settle
        imaging_frames = [f for f in all_frames if not is_during_settle(f[0], settle_windows)]

        if imaging_frames:
            imaging_ra = [f[1] for f in imaging_frames]
            imaging_dec = [f[2] for f in imaging_frames]

            # Apply outlier filtering to imaging frames too
            imaging_ra_clean = filter_outliers_iqr(imaging_ra)
            imaging_dec_clean = filter_outliers_iqr(imaging_dec)
            imaging_outliers = len(imaging_ra) - len(imaging_ra_clean) + len(imaging_dec) - len(imaging_dec_clean)

            imaging_ra_rms_px = math.sqrt(sum(r * r for r in imaging_ra_clean) / len(imaging_ra_clean)) if imaging_ra_clean else 0
            imaging_dec_rms_px = math.sqrt(sum(d * d for d in imaging_dec_clean) / len(imaging_dec_clean)) if imaging_dec_clean else 0
            imaging_total_rms_px = math.sqrt(imaging_ra_rms_px**2 + imaging_dec_rms_px**2)

            result['stats']['imaging'] = {
                'ra_rms_as': round(imaging_ra_rms_px * ps, 3),
                'dec_rms_as': round(imaging_dec_rms_px * ps, 3),
                'total_rms_as': round(imaging_total_rms_px * ps, 3),
                'frame_count': len(imaging_frames),
                'outliers_removed': imaging_outliers
            }

    # Store "all frames" stats under 'all' key for consistency
    result['stats']['all'] = {
        'ra_rms_as': result['stats']['ra_rms_as'],
        'dec_rms_as': result['stats']['dec_rms_as'],
        'total_rms_as': result['stats']['total_rms_as'],
        'frame_count': len(all_frames),
        'outliers_removed': outliers_removed
    }

    # --- Decimation: Apply LTTB to reduce data size ---
    DECIMATION_THRESHOLD = 1000
    original_frames_count = len(result['frames'])
    original_rms_count = len(result['rms'])

    if original_frames_count > DECIMATION_THRESHOLD:
        result['frames'] = lttb_downsample(result['frames'], DECIMATION_THRESHOLD, x_idx=0, y_idx=1)
        result['stats']['frames_original_count'] = original_frames_count
        result['stats']['frames_decimated'] = True

    if original_rms_count > DECIMATION_THRESHOLD:
        result['rms'] = lttb_downsample(result['rms'], DECIMATION_THRESHOLD, x_idx=0, y_idx=3)  # Use total_rms for shape
        result['stats']['rms_original_count'] = original_rms_count
        result['stats']['rms_decimated'] = True

    # --- Precision reduction: Round all floats to 4 decimal places ---
    def round_point(point):
        return [round(v, 4) if isinstance(v, float) else v for v in point]

    result['frames'] = [round_point(f) for f in result['frames']]
    result['rms'] = [round_point(r) for r in result['rms']]

    # Store session start time for clock time display
    if first_session_start:
        result['session_start'] = first_session_start.isoformat()

    return result


def _parse_phd2_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse PHD2 log timestamp formats."""
    ts_str = ts_str.strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S'):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def _empty_asiair_result() -> Dict[str, Any]:
    return {
        'session_start': None,  # ISO datetime string
        'target': None,  # {'ra': str, 'dec': str}
        'shooting_plan': None,  # {'frames': int, 'exposure': float, 'bin': int}
        'exposures': [],
        'dithers': [],
        'af_runs': [],
        'meridian_flips': [],
        'plate_solves': [],
        'autocenters': [],
        'stats': {
            'total_exposures': 0,
            'total_exposure_time_sec': 0,
            'total_time_min': 0,
            'af_count': 0,
            'dither_count': 0,
            'dither_timeout_count': 0,
            'meridian_flip_count': 0,
            'plate_solve_count': 0
        }
    }


def _empty_phd2_result() -> Dict[str, Any]:
    return {
        'pixel_scale': 1.0,
        'session_start': None,  # ISO datetime string of first guiding session
        'frames': [],  # [[h, ra_px, dec_px, snr, ra_guide_dist, dec_guide_dist, ra_dir, dec_dir, ra_dur, dec_dur], ...]
        'rms': [],
        'settle': [],
        'run_bounds': [],
        'stats': {
            'ra_rms_as': 0,
            'dec_rms_as': 0,
            'total_rms_as': 0,
            'ra_rms_px': 0,
            'dec_rms_px': 0,
            'total_rms_px': 0,
            'total_frames': 0,
            'dither_count': 0,
            'settle_success_count': 0,
            'settle_timeout_count': 0
        }
    }


# --- NINA Log Parser ---

def parse_nina_log(content: str) -> Dict[str, Any]:
    """
    Parse NINA log content string and extract AutoFocus, equipment, and timeline data.

    NINA logs use pipe-delimited format: |INFO| |WARNING| |ERROR|
    with timestamps and source filenames like "FocuserMediator.cs".

    Returns:
        {
            'nina_version': str | None,
            'os_info': str | None,
            'session_start': str | None,    # ISO datetime string
            'session_end': str | None,      # ISO datetime string
            'autofocus_runs': [             # list, may be empty
                {
                    'run_index': int,           # 1-based
                    'filter': str | None,       # filter name if detectable
                    'trigger': str | None,      # e.g. "AutofocusAfterFilterChange" or "Manual"
                    'start_time': str | None,   # ISO datetime string
                    'status': 'success' | 'failed',
                    'final_position': int | None,
                    'best_hfr': float | None,
                    'best_stars': int | None,
                    'fitting_method': str | None,   # e.g. "Hyperbolic"
                    'r_squared': float | None,      # only present on failed runs
                    'r_squared_threshold': float | None,
                    'restored_position': int | None,  # position restored to on failure
                    'temperature': float | None,    # focuser temp at completion
                    'steps': [                  # all measured V-curve points
                        {
                            'position': int,
                            'hfr': float | None,    # None = no stars detected
                            'hfr_sigma': float | None,
                            'star_count': int
                        }
                    ],
                    'no_star_steps': int,       # count of steps with 0 stars
                    'failure_reason': str | None  # human-readable, only on failed runs
                }
            ],
            'equipment': {
                'camera': str | None,
                'mount': str | None,
                'filter_wheel': str | None,
                'focuser': str | None,
                'rotator': str | None,
                'guider': str | None,
                'dome': str | None,
                'weather': str | None,
                'power': str | None,
                'plugins': str | None
            },
            'timeline_phases': [
                {
                    'phase': str,
                    'badge_class': str,         # startup/imaging/focus/platesolve/guiding/flats/sequence
                    'start_time': str | None,
                    'end_time': str | None,
                    'status': 'ok' | 'failed' | 'cancelled' | None,
                    'error_count': int,
                    'warning_count': int,
                    'events': [
                        {
                            'time': str | None,
                            'level': 'INFO' | 'WARNING' | 'ERROR',
                            'message': str
                        }
                    ]
                }
            ],
            'flat_summary': [
                {
                    'filter': str,
                    'frame_count': int,
                    'exposure_s': float | None,
                    'temperature_c': float | None
                }
            ],
            'errors': [str],                # list of log-level ERROR messages
            'warnings': [str],              # list of log-level WARNING messages
            'parse_warnings': [str],        # parser-level issues
            'partial': bool                 # True if file parsed but some data is missing
        }
    """
    import logging
    logger = logging.getLogger(__name__)

    result = _empty_nina_result()
    parse_warnings = []

    if not content or not content.strip():
        result['partial'] = False
        result['parse_warnings'].append(_('Empty log file.'))
        return result

    lines = content.splitlines()
    if len(lines) > 10000:
        parse_warnings.append(_('Log file is very large, truncated for performance.'))
        lines = lines[:10000]

    # Detection heuristic: check for pipe-delimited format AND NINA header
    has_pipe_delimiter = any('|INFO|' in line or '|WARNING|' in line or '|ERROR|' in line for line in lines[:100])
    has_nina_header = any('N.I.N.A' in line or 'NINA' in line for line in lines[:100])

    if not has_pipe_delimiter and not has_nina_header:
        result['partial'] = False
        result['parse_warnings'].append(_('File does not appear to be a NINA log.'))
        return result

    session_start = None
    session_end = None
    current_af_run = None
    af_run_counter = 0
    current_phase = None
    phase_event_count = 0
    current_span_phase = None  # Track span phases (imaging, guiding, platesolve, sequence)

    # Helper function to close span phases (imaging, guiding, platesolve)
    def close_span_phase(ts):
        nonlocal current_span_phase
        if current_span_phase is not None:
            current_span_phase['end_time'] = ts.isoformat()
            result['timeline_phases'].append(current_span_phase)
            current_span_phase = None

    # Track recent events for cross-referencing
    recent_filter = None
    recent_filter_time = None
    last_focus_position = None
    pending_af_trigger = None  # BUG 2 FIX: Track trigger from "Starting Trigger:" line

    # Track which lines have been consumed for HFR look-ahead to avoid double-assignment
    hfr_consumed_lines = set()

    # Regex patterns for NINA log parsing
    # Note: Python 3.13 has issues with \d{ patterns, using [0-9] instead
    NINA_PATTERNS = {
        'timestamp': re.compile(r'^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})'),
        'version': re.compile(r'N\.I\.N\.A\.\s+(?:Nighttime)?Imaging|version[:\s]+([0-9.]+)', re.IGNORECASE),
        'os': re.compile(r'Operating system[:\s]+(.+)', re.IGNORECASE),
        'af_start': re.compile(r'BroadcastAutoFocusRunStarting', re.IGNORECASE),
        'af_success': re.compile(r'BroadcastSuccessfulAutoFocusRun', re.IGNORECASE),
        'af_temp': re.compile(r'Temperature[:\s]+([\d.-]+)', re.IGNORECASE),
        'af_position': re.compile(r'(?:MoveFocuserInternal|position)[:\s]+([0-9]+)', re.IGNORECASE),
        'af_restore': re.compile(r'StartAutoFocus.*Restoring original focus position', re.IGNORECASE),
        'af_r_squared': re.compile(r'R\u00B2.*?below threshold\.\s+([0-9.-]+)\s*/\s*([0-9.]+)', re.IGNORECASE),
        'af_stars': re.compile(r'Average HFR[:\s]+([0-9.]+),?\s*HFR\s*σ[:\s]*([0-9.]+),?\s*Detected Stars ([0-9]+)', re.IGNORECASE),
        'af_stars_alt': re.compile(r'HFR[:\s]*[=:]+?\s*([0-9.]+)', re.IGNORECASE),  # Fallback pattern - handles "HFR: X" or "HFR = X" or "HFR X"
        'af_stars_no_detect': re.compile(r'No stars detected', re.IGNORECASE),
        'af_method': re.compile(r'for\s+(\w+)\s+Fitting', re.IGNORECASE),  # FIX 3: Matches "for Hyperbolic Fitting" in failure lines
        'filter_change': re.compile(r'Moving to Filter\s+["\']?([\w-]+)["\']?', re.IGNORECASE),  # FIX 1a: Matches "Moving to Filter O-III at Position 5"
        'filter_capture': re.compile(r'Filter:\s*([\w-]+)[;,\s]', re.IGNORECASE),  # FIX 1b: Captures filter from camera exposure lines like "Filter: Lum; Gain: 100;..."
        'af_trigger_line': re.compile(r'Starting Trigger:\s+(AutofocusAfterFilterChange|Manual\s+autofocus)', re.IGNORECASE),  # Pattern for trigger line before AF start
        'af_trigger': re.compile(r'AutofocusAfterFilterChange|Manual\s+autofocus', re.IGNORECASE),
        'equipment': re.compile(r'Equipment\s+(Camera|Mount|FilterWheel|Focuser|Rotator|Guider|Dome|Weather|PowerSwitch|Plugin)[:\s]+(.+)', re.IGNORECASE),
        'flat_start': re.compile(r'Flat\s+Device[\w\s]+started', re.IGNORECASE),
        'flat_frame': re.compile(r'Flat\s+frame.*completed.*Filter\s+["\']?(\w+)["\']?.*exposure[:\s]+([0-9.]+)\s*s', re.IGNORECASE),
        # Span phase patterns (have start and end times)
        'guiding_start': re.compile(r'PHD2.*started guiding|GuiderMediator.*Start', re.IGNORECASE),
        'platesolve_start': re.compile(r'PlateSolving.*Solving|ImageSolver.*Solve', re.IGNORECASE),
        'sequence_start': re.compile(r'SequenceVM.*Starting sequence|Sequence.*started', re.IGNORECASE),
        'imaging_start': re.compile(r'CameraVM\.cs\|Capture\|\d+\|Starting\s+Exposure', re.IGNORECASE),
    }

    # Use enumerated lines for look-ahead capability
    for line_idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # Split pipe-delimited line: DATE|LEVEL|SOURCE|MEMBER|MESSAGE
        parts = line.split('|')
        if len(parts) >= 5:
            date_part = parts[0].strip()
            level_part = parts[1].strip()
            source_part = parts[2].strip()
            member_part = parts[3].strip()
            message_part = '|'.join(parts[4:]).strip() if len(parts) > 4 else ''
        else:
            date_part = ''
            level_part = ''
            source_part = ''
            member_part = ''
            message_part = line

        # Extract timestamp from date part (handles milliseconds)
        ts_match = NINA_PATTERNS['timestamp'].match(date_part)
        if ts_match:
            ts_str = ts_match.group(1)
            try:
                ts = datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                try:
                    # Try parsing with milliseconds
                    ts = datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%S.%f')
                except ValueError:
                    continue

            # Track session time range
            if session_start is None:
                session_start = ts
            session_end = ts

            # Update current phase time range
            if current_phase:
                current_phase['end_time'] = ts.isoformat()

            # Update current span phase time range
            if current_span_phase:
                current_span_phase['end_time'] = ts.isoformat()

        # Extract version info (from message part)
        version_match = NINA_PATTERNS['version'].search(message_part)
        if version_match:
            result['nina_version'] = version_match.group(1) if version_match.lastindex >= 1 else 'Unknown'

        os_match = NINA_PATTERNS['os'].search(message_part)
        if os_match:
            result['os_info'] = os_match.group(1).strip()

        # Extract equipment info (from message part)
        eq_match = NINA_PATTERNS['equipment'].search(message_part)
        if eq_match:
            eq_type = eq_match.group(1)
            eq_value = eq_match.group(2).strip()
            equipment_map = {
                'Camera': 'camera',
                'Mount': 'mount',
                'FilterWheel': 'filter_wheel',
                'Focuser': 'focuser',
                'Rotator': 'rotator',
                'Guider': 'guider',
                'Dome': 'dome',
                'Weather': 'weather',
                'PowerSwitch': 'power'
            }
            if eq_type in equipment_map:
                result['equipment'][equipment_map[eq_type]] = eq_value

        # Extract filter changes (for AF run context)
        filter_match = NINA_PATTERNS['filter_change'].search(message_part)
        if filter_match and ts_match:
            recent_filter = filter_match.group(1)
            recent_filter_time = ts

        # FIX 1: Capture filter from camera exposure lines during active AF run
        # When AF starts on the already-active filter, there's no "Moving to Filter" line.
        # The filter appears in camera exposure lines like: "Filter: Lum; Gain: 100;..."
        filter_capture_match = NINA_PATTERNS['filter_capture'].search(message_part)
        if filter_capture_match and ts_match and current_af_run is not None:
            # Only set recent_filter if it's still None (first capture during AF run)
            if recent_filter is None:
                recent_filter = filter_capture_match.group(1)
                recent_filter_time = ts

        # BUG 2 FIX: Capture trigger from "Starting Trigger:" line (before AF start)
        trigger_line_match = NINA_PATTERNS['af_trigger_line'].search(message_part)
        if trigger_line_match:
            trigger_keyword = trigger_line_match.group(1)
            if 'AfterFilterChange' in trigger_keyword:
                pending_af_trigger = _('After Filter Change')
            elif 'Manual' in trigger_keyword:
                pending_af_trigger = _('Manual')
            else:
                pending_af_trigger = _('Automatic')

        # --- AutoFocus Run Parsing ---

        # AF Run Start
        if NINA_PATTERNS['af_start'].search(member_part):
            af_run_counter += 1
            # Reset HFR consumed lines tracking for this new AF run
            hfr_consumed_lines.clear()

            current_af_run = {
                'run_index': af_run_counter,
                'filter': None,  # BUG 1 FIX: Don't assign filter at creation - assign at run end
                'trigger': None,
                'start_time': ts.isoformat() if ts_match else None,
                'status': 'success',
                'final_position': None,
                'best_hfr': None,
                'best_stars': None,
                'fitting_method': None,
                'r_squared': None,
                'r_squared_threshold': None,
                'restored_position': None,
                'temperature': None,
                'steps': [],
                'no_star_steps': 0,
                'failure_reason': None
            }

            # BUG 2 FIX: Use pending trigger instead of searching message_part
            current_af_run['trigger'] = pending_af_trigger
            pending_af_trigger = None  # Reset after assignment

            # Close any open span phase before starting focus
            if ts_match:
                close_span_phase(ts)

            # Add to timeline
            _add_to_timeline(result, 'Focus', 'focus', ts.isoformat() if ts_match else None,
                           'ok', 0, 0, [f"{_('AutoFocus Run')} {af_run_counter}"])
            continue

        # AF Steps (position measurements) - with look-ahead for HFR
        if current_af_run and NINA_PATTERNS['af_position'].search(line):
            pos_match = NINA_PATTERNS['af_position'].search(line)
            if pos_match:
                position = int(pos_match.group(1))
                last_focus_position = position

                # Try to extract HFR from current line first
                hfr = None
                hfr_sigma = None
                star_count = 0
                star_detected = False

                # Check current line for star detection (primary pattern)
                stars_match = NINA_PATTERNS['af_stars'].search(line)
                if stars_match:
                    hfr = float(stars_match.group(1))
                    if stars_match.lastindex >= 2 and stars_match.group(2):
                        hfr_sigma = float(stars_match.group(2))
                    if stars_match.lastindex >= 3 and stars_match.group(3):
                        star_count = int(stars_match.group(3))
                    star_detected = True
                    hfr_consumed_lines.add(line_idx)
                    logger.debug(f"AF step: position={position}, hfr={hfr} (from current line, primary pattern)")
                else:
                    # Try fallback pattern on current line
                    stars_alt_match = NINA_PATTERNS['af_stars_alt'].search(line)
                    if stars_alt_match:
                        hfr = float(stars_alt_match.group(1))
                        hfr_sigma = None  # Alt pattern doesn't capture sigma
                        star_count = 1
                        star_detected = True
                        hfr_consumed_lines.add(line_idx)
                        logger.debug(f"AF step: position={position}, hfr={hfr} (from current line, fallback pattern)")
                    else:
                        logger.debug(f"AF step: position={position}, no HFR on current line '{line}'")

                # If no HFR found on current line, look ahead in next 5 lines
                if not star_detected:
                    look_ahead_limit = min(line_idx + 6, len(lines))  # +6 for exclusive upper bound
                    for look_idx in range(line_idx + 1, look_ahead_limit):
                        # Skip lines already consumed by previous steps
                        if look_idx in hfr_consumed_lines:
                            continue

                        look_line = lines[look_idx].strip()

                        # Try primary pattern first
                        look_match = NINA_PATTERNS['af_stars'].search(look_line)
                        if look_match:
                            hfr = float(look_match.group(1))
                            if look_match.lastindex >= 2 and look_match.group(2):
                                hfr_sigma = float(look_match.group(2))
                            if look_match.lastindex >= 3 and look_match.group(3):
                                star_count = int(look_match.group(3))
                            star_detected = True
                            hfr_consumed_lines.add(look_idx)  # Mark as consumed
                            logger.debug(f"AF step: position={position}, hfr={hfr} (from look-ahead line {look_idx}, primary pattern)")
                            break  # Only consume first match

                        # Try fallback pattern
                        look_alt_match = NINA_PATTERNS['af_stars_alt'].search(look_line)
                        if look_alt_match:
                            hfr = float(look_alt_match.group(1))
                            hfr_sigma = None
                            star_count = 1
                            star_detected = True
                            hfr_consumed_lines.add(look_idx)  # Mark as consumed
                            logger.debug(f"AF step: position={position}, hfr={hfr} (from look-ahead line {look_idx}, fallback pattern)")
                            break  # Only consume first match

                if not star_detected:
                    logger.debug(f"AF step: position={position}, hfr={hfr} (no HFR found in look-ahead)")

                # Check for "no stars detected" on current line
                if NINA_PATTERNS['af_stars_no_detect'].search(line):
                    star_count = 0
                    current_af_run['no_star_steps'] += 1

                current_af_run['steps'].append({
                    'position': position,
                    'hfr': hfr,
                    'hfr_sigma': hfr_sigma,
                    'star_count': star_count
                })
                continue

        # AF Success (find best focus point)
        if current_af_run and NINA_PATTERNS['af_success'].search(line):
            # Extract temperature from success line
            temp_match = NINA_PATTERNS['af_temp'].search(line)
            if temp_match:
                current_af_run['temperature'] = float(temp_match.group(1))

            # Extract fitting method
            method_match = NINA_PATTERNS['af_method'].search(line)
            if method_match:
                current_af_run['fitting_method'] = method_match.group(1)

            # Find best HFR point from steps
            valid_steps = [s for s in current_af_run['steps'] if s['hfr'] is not None]
            if valid_steps:
                best_step = min(valid_steps, key=lambda s: s['hfr'])
                current_af_run['best_hfr'] = best_step['hfr']
                current_af_run['best_stars'] = best_step['star_count']
                current_af_run['final_position'] = best_step['position']

            # BUG 1 FIX: Assign filter at run end (recent_filter may have been updated during AF run)
            current_af_run['filter'] = recent_filter

            # Close any open span phase before recording focus success
            if ts_match:
                close_span_phase(ts)

            # Record success event
            if ts_match:
                _add_to_timeline(result, 'Focus', 'focus', ts.isoformat(),
                               'ok', 0, 0, [f"{_('AutoFocus Complete')}"])

            # Diagnostic logging
            total_steps = len(current_af_run['steps'])
            valid_hfr_steps = len([s for s in current_af_run['steps'] if s['hfr'] is not None])
            logger.debug(f"AF run #{current_af_run['run_index']}: {total_steps} steps parsed, {valid_hfr_steps} steps with valid HFR")

            result['autofocus_runs'].append(current_af_run)
            current_af_run = None
            continue

        # AF Failure (R squared validation)
        if current_af_run and NINA_PATTERNS['af_r_squared'].search(line):
            r2_match = NINA_PATTERNS['af_r_squared'].search(line)
            if r2_match:
                current_af_run['status'] = 'failed'
                current_af_run['r_squared'] = float(r2_match.group(1))
                if r2_match.lastindex >= 2:
                    current_af_run['r_squared_threshold'] = float(r2_match.group(2))

                # Extract restored position
                restore_match = NINA_PATTERNS['af_restore'].search(line)
                if restore_match and last_focus_position is not None:
                    current_af_run['restored_position'] = last_focus_position

                # FIX 3: Extract fitting method from failure line (format: "for Hyperbolic Fitting")
                method_match = NINA_PATTERNS['af_method'].search(line)
                if method_match:
                    current_af_run['fitting_method'] = method_match.group(1)

                current_af_run['failure_reason'] = _('R squared value below threshold')

                # BUG 1 FIX: Assign filter at run end (recent_filter may have been updated during AF run)
                current_af_run['filter'] = recent_filter

                # Close any open span phase before recording focus failure
                if ts_match:
                    close_span_phase(ts)

                # Add to timeline with failure
                if ts_match:
                    _add_to_timeline(result, 'Focus', 'focus', ts.isoformat(),
                                   'failed', 1, 0, [f"{_('AutoFocus Failed')}"])

                # Diagnostic logging
                total_steps = len(current_af_run['steps'])
                valid_hfr_steps = len([s for s in current_af_run['steps'] if s['hfr'] is not None])
                logger.debug(f"AF run #{current_af_run['run_index']}: {total_steps} steps parsed, {valid_hfr_steps} steps with valid HFR")

            result['autofocus_runs'].append(current_af_run)
            current_af_run = None
            continue

        # --- Flat Frame Parsing ---
        flat_match = NINA_PATTERNS['flat_start'].search(line)
        if flat_match and ts_match:
            # Close any open span phase before starting flats
            close_span_phase(ts)

            current_phase = {
                'phase': _('Flats'),
                'badge_class': 'flats',
                'start_time': ts.isoformat(),
                'end_time': None,
                'status': 'ok',
                'error_count': 0,
                'warning_count': 0,
                'events': [{'time': ts.isoformat(), 'level': 'INFO',
                           'message': _('Flat sequence started')}]
            }
            result['timeline_phases'].append(current_phase)
            phase_event_count = 0
            continue

        flat_frame_match = NINA_PATTERNS['flat_frame'].search(line)
        if flat_frame_match and ts_match and current_phase and current_phase['phase'] == _('Flats'):
            phase_event_count += 1
            # Add to flat summary
            filter_name = flat_frame_match.group(1)
            exposure = float(flat_frame_match.group(2))

            # Check if this filter already in summary
            existing = next((f for f in result['flat_summary'] if f['filter'] == filter_name), None)
            if existing:
                existing['frame_count'] += 1
            else:
                result['flat_summary'].append({
                    'filter': filter_name,
                    'frame_count': 1,
                    'exposure_s': exposure,
                    'temperature_c': current_af_run.get('temperature') if current_af_run else None
                })

            # Update phase end time
            current_phase['end_time'] = ts.isoformat()
            continue

        # --- Span Phase Handling (imaging, guiding, platesolve, sequence) ---

        # Imaging span phase
        imaging_match = NINA_PATTERNS['imaging_start'].search(line)
        if imaging_match and ts_match:
            close_span_phase(ts)
            current_span_phase = {
                'phase': 'Imaging',
                'badge_class': 'imaging',
                'start_time': ts.isoformat(),
                'end_time': None,
                'status': None,
                'error_count': 0,
                'warning_count': 0,
                'events': [{'time': ts.isoformat(), 'level': 'INFO',
                           'message': 'Imaging started'}]
            }

        # Guiding span phase
        guiding_match = NINA_PATTERNS['guiding_start'].search(line)
        if guiding_match and ts_match:
            close_span_phase(ts)
            current_span_phase = {
                'phase': 'Guiding',
                'badge_class': 'guiding',
                'start_time': ts.isoformat(),
                'end_time': None,
                'status': None,
                'error_count': 0,
                'warning_count': 0,
                'events': [{'time': ts.isoformat(), 'level': 'INFO',
                           'message': 'Guiding started'}]
            }

        # Platesolve span phase
        platesolve_match = NINA_PATTERNS['platesolve_start'].search(line)
        if platesolve_match and ts_match:
            close_span_phase(ts)
            current_span_phase = {
                'phase': 'PlateSolve',
                'badge_class': 'platesolve',
                'start_time': ts.isoformat(),
                'end_time': None,
                'status': None,
                'error_count': 0,
                'warning_count': 0,
                'events': [{'time': ts.isoformat(), 'level': 'INFO',
                           'message': 'Plate solving started'}]
            }

        # Sequence span phase (wraps other phases, don't close current span)
        sequence_match = NINA_PATTERNS['sequence_start'].search(line)
        if sequence_match and ts_match:
            # Sequence wraps other phases, track separately
            result['timeline_phases'].append({
                'phase': 'Sequence',
                'badge_class': 'sequence',
                'start_time': ts.isoformat(),
                'end_time': None,
                'status': None,
                'error_count': 0,
                'warning_count': 0,
                'events': [{'time': ts.isoformat(), 'level': 'INFO',
                           'message': 'Sequence started'}]
            })

        # --- Log Level Extraction ---
        level = None
        if '|ERROR|' in line:
            level = 'ERROR'
            result['errors'].append(line.split('|ERROR|')[-1].strip())
        elif '|WARNING|' in line:
            level = 'WARNING'
            result['warnings'].append(line.split('|WARNING|')[-1].strip())
        elif '|INFO|' in line:
            level = 'INFO'

        if level and ts_match:
            # Add to current phase events
            if current_phase:
                if level == 'ERROR':
                    current_phase['error_count'] += 1
                elif level == 'WARNING':
                    current_phase['warning_count'] += 1

                message = line.split(f'|{level}|')[-1].strip()
                current_phase['events'].append({
                    'time': ts.isoformat(),
                    'level': level,
                    'message': message
                })

            # Also add to current span phase events
            if current_span_phase:
                if level == 'ERROR':
                    current_span_phase['error_count'] += 1
                elif level == 'WARNING':
                    current_span_phase['warning_count'] += 1

                message = line.split(f'|{level}|')[-1].strip()
                current_span_phase['events'].append({
                    'time': ts.isoformat(),
                    'level': level,
                    'message': message
                })

    # Close any pending phase
    if current_phase:
        result['timeline_phases'].append(current_phase)

    # Close any remaining span phase
    if current_span_phase and session_end:
        current_span_phase['end_time'] = session_end.isoformat()
        result['timeline_phases'].append(current_span_phase)
        current_span_phase = None

    # Finalize any incomplete AF runs
    if current_af_run:
        # Finalize AF run without success/failure message
        # This can happen with partial logs or interrupted sessions
        total_steps = len(current_af_run['steps'])
        valid_hfr_steps = len([s for s in current_af_run['steps'] if s['hfr'] is not None])
        if valid_hfr_steps > 0:
            # Find best HFR point from steps
            valid_steps = [s for s in current_af_run['steps'] if s['hfr'] is not None]
            if valid_steps:
                best_step = min(valid_steps, key=lambda s: s['hfr'])
                current_af_run['best_hfr'] = best_step['hfr']
                current_af_run['best_stars'] = best_step['star_count']
                current_af_run['final_position'] = best_step['position']

        # BUG 1 FIX: Assign filter at run end for incomplete runs
        current_af_run['filter'] = recent_filter

        logger.debug(f"Finalizing incomplete AF run #{current_af_run['run_index']}: {total_steps} total steps, {valid_hfr_steps} steps with valid HFR")
        result['autofocus_runs'].append(current_af_run)
        current_af_run = None

    # Store session time range
    if session_start:
        result['session_start'] = session_start.isoformat()
    if session_end:
        result['session_end'] = session_end.isoformat()

    # Check for partial data
    result['partial'] = (
        len(result['autofocus_runs']) == 0 and
        len(result['timeline_phases']) == 0 and
        len(result['flat_summary']) == 0
    )

    if result['partial'] and not parse_warnings:
        parse_warnings.append(_('Log parsed but no structured data found.'))

    result['parse_warnings'] = parse_warnings

    return result


def _add_to_timeline(result: Dict[str, Any], phase_name: str, badge_class: str,
                   start_time: str, status: str, error_count: int,
                   warning_count: int, events: list):
    """Helper to add a phase to timeline."""
    if not events:
        events = []

    result['timeline_phases'].append({
        'phase': phase_name,
        'badge_class': badge_class,
        'start_time': start_time,
        'end_time': None,  # FIX: Use None for single-point events (focus/AF runs)
        'status': status,
        'error_count': error_count,
        'warning_count': warning_count,
        'events': [{'time': start_time, 'level': 'INFO', 'message': e} for e in events]
    })


def _empty_nina_result() -> Dict[str, Any]:
    """Return empty NINA result structure."""
    return {
        'nina_version': None,
        'os_info': None,
        'session_start': None,
        'session_end': None,
        'autofocus_runs': [],
        'equipment': {
            'camera': None,
            'mount': None,
            'filter_wheel': None,
            'focuser': None,
            'rotator': None,
            'guider': None,
            'dome': None,
            'weather': None,
            'power': None,
            'plugins': None
        },
        'timeline_phases': [],
        'flat_summary': [],
        'errors': [],
        'warnings': [],
        'parse_warnings': [],
        'partial': False
    }
