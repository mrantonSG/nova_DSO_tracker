"""
Regression tests for the /analytics dashboard window-boundary fix.

Root cause being guarded against: date windows computed as
`date >= today - timedelta(days=N)` with no upper bound produce N+1
calendar days (today and today-N both inclusive) instead of N, while the
UI labels still say N. These tests seed AnalyticsEvent rows directly into
the in-memory test database and inspect the exact kwargs
analytics_dashboard() passes to render_template(), rather than parsing the
rendered HTML.
"""
import pytest
from datetime import date, timedelta

import nova.blueprints.core as core_module
from nova.models import AnalyticsEvent

ANALYTICS_SECRET = "test-analytics-secret"


@pytest.fixture
def analytics_client(su_client_logged_in, db_session, monkeypatch):
    """
    A logged-in test client wired so /analytics reads from the same
    in-memory SQLite database the test seeds.

    su_client_logged_in / db_session (conftest.py) patch nova.SessionLocal
    to a scoped_session bound to an in-memory engine. nova/blueprints/core.py
    imports SessionLocal by value (`from nova.models import SessionLocal`),
    so that name is bound at import time and isn't covered by the
    nova.SessionLocal patch alone - it needs its own patch here.
    """
    import nova
    monkeypatch.setattr(core_module, 'SessionLocal', nova.SessionLocal)
    monkeypatch.setenv('ANALYTICS_SECRET', ANALYTICS_SECRET)
    return su_client_logged_in


@pytest.fixture
def captured_context(monkeypatch):
    """Captures the kwargs analytics_dashboard() passes to render_template."""
    captured = {}

    def fake_render_template(template_name, **kwargs):
        captured.update(kwargs)
        return "OK"

    monkeypatch.setattr(core_module, 'render_template', fake_render_template)
    return captured


def seed_event(db_session, event_name, day, count):
    db_session.add(AnalyticsEvent(event_name=event_name, date=day, count=count))


def seed_consecutive_days(db_session, event_name, end_day, num_days, count=1):
    """Seed `num_days` consecutive days ending at (and including) end_day."""
    for i in range(num_days):
        seed_event(db_session, event_name, end_day - timedelta(days=i), count)


def get_analytics_context(client):
    resp = client.get(f'/analytics?secret={ANALYTICS_SECRET}')
    assert resp.status_code == 200
    return resp


def test_active_usage_days_30_window_is_exact(analytics_client, db_session, captured_context):
    """Exactly 30 consecutive days (today .. today-29) -> active_days_30 == 30."""
    today = date.today()
    seed_consecutive_days(db_session, 'dashboard_load', today, 30, count=1)
    db_session.commit()

    get_analytics_context(analytics_client)

    assert captured_context['active_days_30'] == 30


def test_future_dated_row_excluded_from_30_and_90_day_windows(analytics_client, db_session, captured_context):
    """A row dated tomorrow must not inflate the 30-day or 90-day counts."""
    today = date.today()
    seed_consecutive_days(db_session, 'dashboard_load', today, 30, count=1)
    seed_event(db_session, 'dashboard_load', today + timedelta(days=1), count=5)
    db_session.commit()

    get_analytics_context(analytics_client)

    assert captured_context['active_days_30'] == 30

    events_by_name = {e['event_name']: e for e in captured_context['events']}
    dashboard_load = events_by_name['dashboard_load']
    assert dashboard_load['total'] == 30
    assert dashboard_load['active_days'] == 30


def test_feature_usage_90_day_window_is_exact(analytics_client, db_session, captured_context):
    """Exactly 90 consecutive days (today .. today-89) -> full total/active_days,
    and a 91st-day-back row must not be included."""
    today = date.today()
    for i in range(90):
        seed_event(db_session, 'test_feature', today - timedelta(days=i), count=2)
    # One day older than the window - must be excluded.
    seed_event(db_session, 'test_feature', today - timedelta(days=90), count=100)
    db_session.commit()

    get_analytics_context(analytics_client)

    events_by_name = {e['event_name']: e for e in captured_context['events']}
    test_feature = events_by_name['test_feature']
    assert test_feature['total'] == 90 * 2
    assert test_feature['active_days'] == 90


def test_daily_chart_covers_90_days_ending_today(analytics_client, db_session, captured_context):
    """The chart series must be exactly 90 entries long and end on today."""
    today = date.today()
    seed_event(db_session, 'dashboard_load', today, count=3)
    db_session.commit()

    get_analytics_context(analytics_client)

    login_series = captured_context['login_series']
    assert len(login_series) == 90
    assert login_series[-1]['date_str'] == today.strftime('%Y-%m-%d')
    assert login_series[0]['date_str'] == (today - timedelta(days=89)).strftime('%Y-%m-%d')


def test_total_dashboard_views_matches_chart_bar_sum(analytics_client, db_session, captured_context):
    """Total Dashboard Views stat must equal the sum of the visible chart bars,
    even in the presence of an out-of-window future row."""
    today = date.today()
    seed_consecutive_days(db_session, 'dashboard_load', today, 90, count=2)
    seed_event(db_session, 'dashboard_load', today + timedelta(days=1), count=50)
    db_session.commit()

    get_analytics_context(analytics_client)

    login_series = captured_context['login_series']
    bar_sum = sum(entry['count'] for entry in login_series)
    assert captured_context['total_logins'] == bar_sum
    assert captured_context['total_logins'] == 90 * 2
