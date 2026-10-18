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

"""PostgreSQL implementation of ``PublicPageViewRepository`` (spec #103).

Schema in ``postgres_schema.py`` and alembic ``0020_public_page_daily_views``.
"""

from __future__ import annotations

from datetime import date

from structlog import get_logger

from ..utils.postgres_ext import connect
from .public_page_view_repository import PublicPageViewRepository

logger = get_logger(__name__)


class PostgresPublicPageViewRepository(PublicPageViewRepository):
    """PostgreSQL-backed public page counter."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres public page view repository")

    def increment(self, day: date, kind: str, object_id: str) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO public_page_daily_views (day, kind, object_id, views) VALUES (%s, %s, %s, 1)
                ON CONFLICT (day, kind, object_id) DO UPDATE SET views = public_page_daily_views.views + 1
                """,
                (day, kind, object_id),
            )
