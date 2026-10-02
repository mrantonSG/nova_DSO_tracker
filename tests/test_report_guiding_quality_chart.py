from nova.report_graphs import generate_guiding_quality_chart

LIMITS = (0.48, 0.72, 1.44, 2.16)

# rms rows: [h, ra_rms_as, dec_rms_as, total_rms_as]
VALID_PHD2 = {'rms': [[0.0, 0.3, 0.4, 0.5], [0.5, 0.4, 0.5, 0.64], [1.0, 0.5, 0.6, 0.78]]}


def test_returns_image_for_valid_series_with_limits():
    result = generate_guiding_quality_chart(VALID_PHD2, LIMITS)
    assert isinstance(result, str)
    assert result


def test_returns_none_without_limits():
    assert generate_guiding_quality_chart(VALID_PHD2, None) is None


def test_returns_none_for_fewer_than_two_points():
    assert generate_guiding_quality_chart({'rms': [[0.0, 0.3, 0.4, 0.5]]}, LIMITS) is None
