"""
nova/log_parser.py - Parse ASIAIR and PHD2 logs for session analysis.

Returns structured data for Chart.js visualization matching the RAW structure
from the reference session_dashboard.jsx implementation.
"""
import re
import math
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple


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
        autocenter_match = ASIAIR_PATTERNS['autocenter_distance'].search(line)
        if autocenter_match:
            result['autocenters'].append({
                'h': round(hours_elapsed, 4),
                'ts': dt.isoformat(),
                'distance_pct': float(autocenter_match.group(1)),
                'distance_deg': float(autocenter_match.group(2)),
                'centered': 'centered' in line.lower()
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
            if 'settling complete' in lower_line or 'complete' in lower_line:
                result['stats']['settle_success_count'] += 1
            elif 'timeout' in lower_line or 'failed' in lower_line:
                result['stats']['settle_timeout_count'] += 1
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

    # Compute overall stats
    all_ra = [f[1] for f in all_frames]
    all_dec = [f[2] for f in all_frames]

    result['stats']['ra_rms_px'] = round(math.sqrt(sum(r * r for r in all_ra) / len(all_ra)), 3)
    result['stats']['dec_rms_px'] = round(math.sqrt(sum(d * d for d in all_dec) / len(all_dec)), 3)
    result['stats']['total_rms_px'] = round(math.sqrt(sum(r * r + d * d for r, d in zip(all_ra, all_dec)) / len(all_ra)), 3)

    result['stats']['ra_rms_as'] = round(result['stats']['ra_rms_px'] * ps, 3)
    result['stats']['dec_rms_as'] = round(result['stats']['dec_rms_px'] * ps, 3)
    result['stats']['total_rms_as'] = round(result['stats']['total_rms_px'] * ps, 3)
    result['stats']['total_frames'] = len(all_frames)

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
