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

"""Postgres connection helper (spec #44).

The SQLite repositories open a tuned per-operation connection via
``utils.sqlite_ext.connect``. This is the Postgres analogue: a thin
``connect(dsn)`` that hands back a psycopg connection configured so the ported
repository code reads almost identically to the SQLite version —

- ``row_factory=dict_row`` so ``row["column"]`` access works like
  ``sqlite3.Row`` (the SQLite repos index rows by name).
- Used as ``with connect(dsn) as conn:`` — psycopg3's connection context
  manager commits on a clean exit, rolls back on exception, and closes the
  connection, matching the SQLite ``with connect(path) as conn:`` semantics.

Connection-per-operation keeps the port mechanical and the tests hermetic. The
production wiring will front this with ``psycopg_pool.ConnectionPool`` (spec
#44 Target Design) — a drop-in behind the same ``with`` block — but that
lifecycle is deliberately out of this first slice.
"""

from __future__ import annotations

from typing import Optional

import psycopg
from psycopg.rows import dict_row


def connect(dsn: str, *, vector: bool = False) -> psycopg.Connection:
    """Open a psycopg connection with dict rows.

    Returns the connection object itself so callers use it as a context
    manager: ``with connect(dsn) as conn: conn.execute(...)``.

    Args:
        dsn: connection string.
        vector: register the pgvector adapter on this connection so
            numpy arrays / lists bind to ``vector`` columns and reads
            come back as numpy arrays. Only search/chunk code needs it.
    """
    conn = psycopg.connect(dsn, row_factory=dict_row)
    if vector:
        from pgvector.psycopg import register_vector

        register_vector(conn)
    return conn


# ---------------------------------------------------------------------------
# Port conventions (spec #44) — every Postgres repository follows these:
#
#   uuid columns:   pass Python str params (psycopg sends an untyped literal,
#                   PG coerces); READS return uuid.UUID → wrap with as_str().
#   timestamptz:    pass tz-aware datetime objects; reads are tz-aware
#                   datetimes already — no .isoformat()/fromisoformat().
#   boolean:        pass/receive Python bool — no 0/1 mapping.
#   jsonb:          wrap writes in psycopg.types.json.Jsonb; reads are already
#                   list/dict — no json.loads().
#   placeholders:   %s (never ?).
#   upserts:        INSERT ... ON CONFLICT (...) DO UPDATE/NOTHING.
#   generated ids:  INSERT ... RETURNING id (never cursor.lastrowid).
#   LIKE:           SQLite LIKE is ASCII-case-insensitive; use ILIKE when the
#                   query is a user-facing search.
# ---------------------------------------------------------------------------


def as_str(value: object) -> Optional[str]:
    """uuid.UUID → str passthrough for row parsing (None-safe)."""
    return None if value is None else str(value)
