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

"""SQLite implementation of ``PublicPageViewRepository`` (spec #103).

SQLite installs serve no public pages in multi-user mode; the table keeps
both backends storing the same facts (FM-6). ``day`` is an ISO date.
"""

from datetime import date
from pathlib import Path

from structlog import get_logger

from ..utils.sqlite_ext import connect
from .public_page_view_repository import PublicPageViewRepository

logger = get_logger(__name__)


class SqlitePublicPageViewRepository(PublicPageViewRepository):
    """SQLite-backed public page counter."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite public page view repository", db_path=str(self.db_path))

    def increment(self, day: date, kind: str, object_id: str) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO public_page_daily_views (day, kind, object_id, views) VALUES (?, ?, ?, 1)
                ON CONFLICT (day, kind, object_id) DO UPDATE SET views = views + 1
                """,
                (day.isoformat(), kind, object_id),
            )
