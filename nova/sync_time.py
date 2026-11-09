"""Time helpers for instance sync.

Times are stored naive and always mean UTC, matching the rest of the schema
(SQLite has no timezone type). Call utc_now() instead of datetime.utcnow() in
sync code so the convention is visible at every call site.
"""

from datetime import datetime, timezone


def utc_now():
    """Current UTC time as a naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
