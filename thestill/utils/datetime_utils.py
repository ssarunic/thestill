# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Canonical timezone-aware datetime helpers (spec #42, FM-3 / FM-6).

The system speaks one datetime dialect: **tz-aware UTC**. All
datetime-producing paths route through these helpers instead of
hand-rolling ``datetime.utcnow()`` / ``datetime.now()`` (which produce
*naive* values); a ruff ``DTZ`` lint rule bans the naive forms so new code
is funnelled here. See spec #42 for the incident that motivated this.
"""

from datetime import datetime, timezone
from typing import Any, Optional


def now_utc() -> datetime:
    """Current time as a timezone-aware UTC datetime.

    Drop-in replacement for the banned ``datetime.utcnow()`` (which returns
    a *naive* value) and ``datetime.now()`` (local, naive).
    """
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Coerce a datetime to tz-aware UTC, leaving ``None`` untouched.

    A tz-naive input is *assumed* to already be UTC (the conventional
    intent for feed/import timestamps) and is stamped with ``tzinfo=utc``
    rather than shifted. An already-aware input is returned unchanged.
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def parse_struct_time_utc(date_tuple: Any) -> datetime:
    """Parse a feedparser date tuple to a tz-aware UTC datetime.

    feedparser normalises ``published_parsed`` to GMT, so the struct_time is
    always UTC; we attach ``tzinfo=utc`` rather than returning a naive value.
    Single canonical implementation shared by the RSS media source and the
    feed manager (FM-6).

    Args:
        date_tuple: feedparser date tuple (``time.struct_time`` or ``None``).

    Returns:
        Parsed UTC datetime, or the current UTC time if parsing fails.
    """
    if date_tuple:
        try:
            # mypy can't tell the unpack won't also bind tzinfo; the ignore is
            # for that false positive, not a real ambiguity.
            return datetime(*date_tuple[:6], tzinfo=timezone.utc)  # type: ignore[misc]
        except (TypeError, ValueError):
            pass
    return now_utc()
