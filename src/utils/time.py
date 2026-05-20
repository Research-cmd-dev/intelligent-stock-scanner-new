"""Centralized UTC time utilities.

All date/time logic in the project should go through these functions to ensure
consistent behavior across local machines, CI, and cloud environments
(e.g. GitHub Codespaces) that may have different local timezones.
"""

from datetime import date as _date, datetime, timezone


def get_current_utc_datetime() -> datetime:
    """Return the current datetime in UTC (timezone-aware)."""
    return datetime.now(tz=timezone.utc)


def get_current_utc_date() -> _date:
    """Return today's date in UTC (not local time)."""
    return get_current_utc_datetime().date()
