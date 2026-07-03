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

"""SQLite → Postgres data migration + parity oracle (spec #44).

Two capabilities, usable together or apart:

1. **migrate** — copy the source-of-truth tables from a SQLite DB into Postgres
   as a *faithful data mirror*. Every non-BLOB column is created as ``text`` and
   values are copied verbatim (stringified). This is deliberately schema-naive:
   it preserves the DATA exactly for validation and backup, but it is NOT the
   app's eventual typed Postgres schema (timestamptz / boolean / jsonb / vector)
   — that lands per-repo across spec #44 Phases 1–4. Derived indexes (the
   sqlite-vec ``vec0`` and FTS5 virtual tables plus their shadow tables) are
   **excluded**: they are rebuilt from source post-migration via ``reindex``,
   not copied.

2. **verify** — a backend-agnostic parity check: for each table, assert the row
   COUNT and an order-independent CONTENT HASH match between SQLite and Postgres.
   This is the regression oracle for the migration ("compare both after") and,
   longer term, the gate each ported repo must pass against real prod data.

The content hash normalises every value identically on both sides (see
``_canon``) so native SQLite types and the text mirror compare equal. The hash
is a sum of per-row MD5 integers: order-independent AND duplicate-sensitive.

CLI::

    python -m thestill.utils.db_migration migrate \\
        --sqlite data/podcasts.db --postgres postgresql://u@h:5432/db
    python -m thestill.utils.db_migration verify \\
        --sqlite data/podcasts.db --postgres postgresql://u@h:5432/db
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Iterable, Optional

# psycopg is imported lazily inside the functions that touch Postgres so that
# importing this module (and running its SQLite-only helpers/tests) never
# requires the optional [postgres] extra.


_NULL_SENTINEL = "\x00NULL\x00"


def _strip_nul(text: str) -> str:
    """Drop embedded U+0000 — storable in SQLite TEXT, forbidden in PG text.

    Defense-in-depth: the pipeline sanitizes LLM output at the clean stage
    (utils/text_sanitizer), so stored NULs shouldn't exist — but a stray one
    must degrade to a 1-char loss, not abort a whole-table COPY. Applied
    identically in ``_canon`` so the parity hash compares the same content
    on both engines.
    """
    return text.replace("\x00", "") if "\x00" in text else text


def _canon(value: object) -> str:
    """Canonical string form of a cell, identical for SQLite and the PG mirror.

    ``None`` → sentinel; ``bytes`` → ``0x<hex>``; everything else → ``str()``.
    Because the mirror stores non-BLOB columns as text, a SQLite integer ``5``
    and its mirrored text ``'5'`` both canonicalise to ``'5'`` and hash equal.
    Embedded NUL is dropped to mirror what the COPY path stores (see
    ``_strip_nul``).
    """
    if value is None:
        return _NULL_SENTINEL
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    if isinstance(value, str):
        return _strip_nul(value)
    return str(value)


def _row_int(cols: Iterable[str]) -> int:
    """MD5 of a canonicalised row, as an int (for an order-independent sum)."""
    joined = "\x1f".join(_canon(c) for c in cols)
    return int.from_bytes(hashlib.md5(joined.encode("utf-8", "surrogatepass")).digest(), "big")


@dataclass
class TableReport:
    table: str
    sqlite_rows: int = 0
    pg_rows: int = 0
    sqlite_hash: int = 0
    pg_hash: int = 0

    @property
    def ok(self) -> bool:
        return self.sqlite_rows == self.pg_rows and self.sqlite_hash == self.pg_hash


@dataclass
class ParityReport:
    tables: list[TableReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(t.ok for t in self.tables)

    @property
    def mismatches(self) -> list[TableReport]:
        return [t for t in self.tables if not t.ok]

    def summary(self) -> str:
        lines = []
        for t in self.tables:
            mark = "ok " if t.ok else "MISMATCH"
            extra = (
                ""
                if t.ok
                else f"  (sqlite {t.sqlite_rows}r/{t.sqlite_hash % 10**8:08d} vs pg {t.pg_rows}r/{t.pg_hash % 10**8:08d})"
            )
            lines.append(f"  [{mark}] {t.table:34} rows={t.sqlite_rows}{extra}")
        verdict = "PARITY OK" if self.ok else f"PARITY FAILED — {len(self.mismatches)} table(s) differ"
        return "\n".join([*lines, verdict])


def discover_source_tables(sconn: sqlite3.Connection) -> list[str]:
    """Source-of-truth tables to migrate: excludes SQLite internals, virtual
    (FTS/vec0) tables, and their shadow tables (derived indexes, rebuilt later).
    """
    rows = sconn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
    virtual = {name for name, sql in rows if (sql or "").lstrip().upper().startswith("CREATE VIRTUAL")}

    def is_shadow(name: str) -> bool:
        return any(name.startswith(v + "_") for v in virtual)

    out = []
    for name, _sql in rows:
        if name.startswith("sqlite_"):
            continue
        if name in virtual or is_shadow(name):
            continue
        out.append(name)
    return sorted(out)


def _columns(sconn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    """(column_name, declared_type_upper) in table order."""
    return [(r[1], (r[2] or "").upper()) for r in sconn.execute(f'PRAGMA table_info("{table}")')]


def _pg_coltype(decl: str) -> str:
    # Faithful data mirror: BLOB → bytea so binary survives; everything else is
    # text (SQLite is dynamically typed, so a uniform text mirror is safest).
    return "bytea" if "BLOB" in decl else "text"


def _coerce_for_copy(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return _strip_nul(str(value))


# The mirror lives in its own Postgres schema, NEVER in ``public``. The app's
# typed tables (PostgresUserRepository et al.) own ``public``; a mirror table
# with the same name would otherwise clobber them via DROP TABLE ... CASCADE.
MIRROR_SCHEMA = "sqlite_mirror"


def migrate_table(sconn: sqlite3.Connection, pconn, table: str) -> int:
    """(Re)create ``table`` in the mirror schema and copy all rows via COPY.
    Returns the number of rows copied."""
    cols = _columns(sconn, table)
    col_defs = ", ".join(f'"{name}" {_pg_coltype(decl)}' for name, decl in cols)
    col_list = ", ".join(f'"{name}"' for name, _ in cols)
    qualified = f'"{MIRROR_SCHEMA}"."{table}"'

    with pconn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{MIRROR_SCHEMA}"')
        cur.execute(f"DROP TABLE IF EXISTS {qualified} CASCADE")
        cur.execute(f"CREATE TABLE {qualified} ({col_defs})")
        copied = 0
        with cur.copy(f"COPY {qualified} ({col_list}) FROM STDIN") as copy:
            for row in sconn.execute(f'SELECT {col_list} FROM "{table}"'):
                copy.write_row([_coerce_for_copy(v) for v in row])
                copied += 1
    pconn.commit()
    return copied


def migrate_all(sqlite_path: str, dsn: str, tables: Optional[list[str]] = None) -> dict[str, int]:
    """Migrate the source tables (or the given subset) into Postgres. Returns
    {table: rows_copied}."""
    import psycopg

    result: dict[str, int] = {}
    sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        target = tables or discover_source_tables(sconn)
        with psycopg.connect(dsn) as pconn:
            for t in target:
                result[t] = migrate_table(sconn, pconn, t)
    finally:
        sconn.close()
    return result


def _sqlite_table_stats(sconn: sqlite3.Connection, table: str) -> tuple[int, int]:
    cols = [c for c, _ in _columns(sconn, table)]
    col_list = ", ".join(f'"{c}"' for c in cols)
    count = 0
    running = 0
    for row in sconn.execute(f'SELECT {col_list} FROM "{table}"'):
        running += _row_int(row)
        count += 1
    return count, running


def _pg_table_stats(pconn, sconn: sqlite3.Connection, table: str) -> tuple[int, int]:
    # Read the SAME column order as SQLite so the row hash lines up.
    cols = [c for c, _ in _columns(sconn, table)]
    col_list = ", ".join(f'"{c}"' for c in cols)
    count = 0
    running = 0
    with pconn.cursor() as cur:
        cur.execute(f'SELECT {col_list} FROM "{MIRROR_SCHEMA}"."{table}"')
        for row in cur:
            running += _row_int(row)
            count += 1
    return count, running


def verify_parity(sqlite_path: str, dsn: str, tables: Optional[list[str]] = None) -> ParityReport:
    """Compare row count + content hash per table between SQLite and Postgres."""
    import psycopg

    report = ParityReport()
    sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        target = tables or discover_source_tables(sconn)
        with psycopg.connect(dsn) as pconn:
            for t in target:
                s_rows, s_hash = _sqlite_table_stats(sconn, t)
                p_rows, p_hash = _pg_table_stats(pconn, sconn, t)
                report.tables.append(
                    TableReport(table=t, sqlite_rows=s_rows, pg_rows=p_rows, sqlite_hash=s_hash, pg_hash=p_hash)
                )
    finally:
        sconn.close()
    return report


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="SQLite → Postgres data mirror + parity check (spec #44)")
    parser.add_argument("mode", choices=["migrate", "verify", "tables"])
    parser.add_argument("--sqlite", required=True, help="Path to the SQLite DB")
    parser.add_argument("--postgres", help="Postgres DSN (not needed for 'tables')")
    parser.add_argument("--tables", help="Comma-separated subset (default: all source tables)")
    args = parser.parse_args(argv)
    subset = args.tables.split(",") if args.tables else None

    # CLI output goes to stdout via sys.stdout.write — this is an operator
    # tool whose report must be pipeable/greppable, not a log stream. The
    # repo's T201 print ban still applies, hence the explicit writes.
    out = sys.stdout.write

    if args.mode == "tables":
        conn = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
        for t in discover_source_tables(conn):
            out(f"{t}\n")
        conn.close()
        return 0

    if not args.postgres:
        parser.error("--postgres is required for migrate/verify")

    if args.mode == "migrate":
        counts = migrate_all(args.sqlite, args.postgres, subset)
        total = sum(counts.values())
        out(f"Migrated {len(counts)} tables, {total} rows.\n")

    report = verify_parity(args.sqlite, args.postgres, subset)
    out(report.summary() + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
