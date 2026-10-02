"""
Parser tests for the PHD2 'rms_imaging' series: rolling 30-frame Total RMS
over frames outside settle windows, used by the Guiding Quality charts.
"""

import math

from nova.log_parser import parse_phd2_log

HEADER = [
    "PHD2 version 2.6.13, Log version 2.5. Log enabled at 2025-01-01 21:00:00",
    "",
    "Guiding Begins at 2025-01-01 21:00:00",
    "Pixel scale = 1.00 arc-sec/px, Binning = 1, Focal length = 400 mm",
    "Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,SNR",
]

NORMAL = 0.3   # px, RA and Dec -> Total 0.424"
SPIKE = 5.0    # px, RA and Dec during the dither


def _frame(n, value):
    return f'{n},{n * 2.0:.3f},"Mount",{value},{value},{value},{value},40.0'


def _build_log(with_settle):
    lines = list(HEADER)
    for n in range(1, 101):
        if with_settle and n == 61:
            lines.append("INFO: SETTLING STATE CHANGE, Settling started")
        # Spike on frames 61-68; frames 69-70 are back within tolerance, as in
        # real logs where settling completes only once the error has settled
        in_dither = with_settle and 61 <= n <= 68
        lines.append(_frame(n, SPIKE if in_dither else NORMAL))
        if with_settle and n == 70:
            lines.append("INFO: SETTLING STATE CHANGE, Settling complete")
    lines.append("Guiding Ends at 2025-01-01 21:03:20")
    return "\n".join(lines) + "\n"


def test_rms_imaging_excludes_settle_spike():
    result = parse_phd2_log(_build_log(with_settle=True))

    assert result['settle_windows'], "fixture must produce a settle window"

    rms_peak = max(r[3] for r in result['rms'])
    assert rms_peak > 3.0  # the dither spike is in the existing series

    assert result['rms_imaging']
    assert all(len(p) == 2 for p in result['rms_imaging'])
    imaging_peak = max(p[1] for p in result['rms_imaging'])
    assert imaging_peak < rms_peak
    assert math.isclose(imaging_peak, round(math.sqrt(2 * NORMAL ** 2), 3), abs_tol=1e-3)


def test_rms_imaging_without_settle_uses_all_frames():
    result = parse_phd2_log(_build_log(with_settle=False))

    assert result['settle_windows'] == []
    assert result['rms_imaging']
    assert result['rms_imaging'] == [[r[0], r[3]] for r in result['rms']]


def test_empty_log_has_empty_rms_imaging():
    assert parse_phd2_log("")['rms_imaging'] == []


def test_header_only_log_has_empty_rms_imaging():
    content = "\n".join(HEADER + ["Guiding Ends at 2025-01-01 21:00:00"]) + "\n"
    result = parse_phd2_log(content)

    assert result['rms_imaging'] == []
    assert result['rms'] == []
