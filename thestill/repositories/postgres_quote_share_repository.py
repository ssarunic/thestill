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

"""PostgreSQL implementation of ``QuoteShareRepository`` (spec #114).

Schema in ``postgres_schema.py`` and alembic ``0031_quote_shares``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Sequence

from ..utils.postgres_ext import connect
from .quote_share_repository import QuoteShareRepository


class PostgresQuoteShareRepository(QuoteShareRepository):
    placeholder = "%s"
    _is_distinct_from = "IS DISTINCT FROM EXCLUDED"
    _contains = "strpos({haystack}, {needle}) > 0"

    def __init__(self, dsn: str):
        self.dsn = dsn

    def _fetch(self, sql: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
        with connect(self.dsn) as conn:
            return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    def _execute(self, sql: str, params: Sequence[Any]) -> int:
        with connect(self.dsn) as conn:
            return conn.execute(sql, tuple(params)).rowcount

    def _executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        with connect(self.dsn) as conn:
            with conn.cursor() as cursor:
                cursor.executemany(sql, [tuple(row) for row in rows])

    def _timestamp(self, moment: datetime) -> Any:
        return moment

    def _day(self, day: date) -> Any:
        return day
