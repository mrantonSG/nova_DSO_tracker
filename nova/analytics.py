"""
Nova DSO Tracker - Anonymous Usage Analytics

GDPR-compliant analytics system for tracking feature usage and login activity.
- No third-party services
- No cookies
- No PII stored (no user identifiers, IPs, or session tokens)
- Only runs in multi-user mode (SINGLE_USER_MODE=False)
- Users can be excluded via ANALYTICS_EXCLUDE_USERS env var
"""
import os
from datetime import date
from flask import current_app
from flask_login import current_user


def _is_excluded() -> bool:
    """Return True if the current user should be excluded from analytics."""
    excluded_raw = os.getenv('ANALYTICS_EXCLUDE_USERS', '')
    # Normalize: strip whitespace and convert to lowercase for comparison
    excluded = [u.strip().lower() for u in excluded_raw.split(',') if u.strip()]
    try:
        if current_user.is_authenticated:
            username_lower = current_user.username.strip().lower()
            return username_lower in excluded
        return False
    except Exception:
        return False


def _is_enabled() -> bool:
    """Analytics only runs in multi-user mode."""
    single_user = os.getenv('SINGLE_USER_MODE', 'True').strip().lower()
    return single_user == 'false'


def record_event(event_name: str) -> None:
    """
    Increment the daily counter for event_name.
    Silent no-op if analytics is disabled, user is excluded, or any error occurs.

    Args:
        event_name: The name of the event to track (e.g., 'dashboard_load', 'journal_open')
    """
    if not _is_enabled() or _is_excluded():
        return
    try:
        from nova.models import AnalyticsEvent, SessionLocal
        from sqlalchemy import select
        today = date.today()
        session = SessionLocal()

        try:
            stmt = select(AnalyticsEvent).where(
                AnalyticsEvent.event_name == event_name,
                AnalyticsEvent.date == today
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row:
                row.count += 1
            else:
                row = AnalyticsEvent(event_name=event_name, date=today, count=1)
                session.add(row)
            session.commit()
        finally:
            session.close()
    except Exception as e:
        try:
            current_app.logger.debug(f"[ANALYTICS] record_event failed: {e}")
        except Exception:
            pass  # Silently fail if logger is not available


def record_login() -> None:
    """
    Increment the daily login counter.
    No user identifier stored — just a count per day.
    """
    if not _is_enabled() or _is_excluded():
        return
    try:
        from nova.models import AnalyticsLogin, SessionLocal
        from sqlalchemy import select
        today = date.today()
        session = SessionLocal()

        try:
            stmt = select(AnalyticsLogin).where(AnalyticsLogin.date == today)
            row = session.execute(stmt).scalar_one_or_none()
            if row:
                row.login_count += 1
            else:
                row = AnalyticsLogin(date=today, login_count=1)
                session.add(row)
            session.commit()
        finally:
            session.close()
    except Exception as e:
        try:
            current_app.logger.debug(f"[ANALYTICS] record_login failed: {e}")
        except Exception:
            pass  # Silently fail if logger is not available
