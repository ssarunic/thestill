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

"""Round-trip + parity tests for the SQLite → Postgres migration tool (spec #44).

Proves the tool on a real Postgres: migrate a representative SQLite DB, assert
parity passes, then tamper the Postgres copy and assert the oracle CATCHES the
divergence (a passing oracle that can't fail is worthless). Also asserts that
derived FTS/vec virtual tables and their shadow tables are excluded from the
source set. Skips when no Postgres is reachable.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from thestill.utils import db_migration

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_reachable(PG_DSN)
pytestmark = pytest.mark.skipif(not PG_OK, reason="Postgres not reachable — set TEST_DATABASE_URL")


def _build_sqlite(path: str) -> None:
    """A DB that exercises the tricky bits: ints, floats, NULLs, unicode, a
    BLOB, duplicate rows, and a derived FTS5 virtual table that must be skipped.
    """
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE podcasts (
            id TEXT PRIMARY KEY, title TEXT, rank INTEGER, score REAL, blob BLOB, note TEXT
        );
        CREATE TABLE followers (id INTEGER PRIMARY KEY, tag TEXT);
        CREATE VIRTUAL TABLE notes_fts USING fts5(body);
        """
    )
    conn.executemany(
        "INSERT INTO podcasts (id, title, rank, score, blob, note) VALUES (?,?,?,?,?,?)",
        [
            ("p1", "Café Décor", 1, 3.5, b"\x00\x01\xff", None),
            ("p2", "Plain", 2, None, None, "dup"),
            ("p3", "Plain", 2, None, None, "dup"),  # duplicate-ish row (diff id)
            ("p4", "emoji 🎧", 99, 0.0, b"", ""),
        ],
    )
    conn.executemany("INSERT INTO followers (tag) VALUES (?)", [("a",), ("b",), ("b",)])
    conn.execute("INSERT INTO notes_fts (body) VALUES ('should be skipped')")
    conn.commit()
    conn.close()


@pytest.fixture
def sqlite_db(tmp_path):
    path = str(tmp_path / "src.db")
    _build_sqlite(path)
    return path


def test_discover_excludes_virtual_and_shadow(sqlite_db):
    conn = sqlite3.connect(sqlite_db)
    tables = db_migration.discover_source_tables(conn)
    conn.close()
    assert set(tables) == {"podcasts", "followers"}
    # No FTS virtual table or its shadow tables leak in.
    assert not any(t.startswith("notes_fts") for t in tables)


def test_migrate_then_parity_passes(sqlite_db):
    counts = db_migration.migrate_all(sqlite_db, PG_DSN)
    assert counts == {"podcasts": 4, "followers": 3}

    report = db_migration.verify_parity(sqlite_db, PG_DSN)
    assert report.ok, report.summary()
    assert {t.table for t in report.tables} == {"podcasts", "followers"}
    # Duplicate-sensitive: followers has two 'b' rows; both must be present.
    assert next(t for t in report.tables if t.table == "followers").pg_rows == 3


def test_parity_oracle_catches_tampering(sqlite_db):
    import psycopg

    db_migration.migrate_all(sqlite_db, PG_DSN)
    # Corrupt one value in the Postgres copy.
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("UPDATE sqlite_mirror.podcasts SET title = 'TAMPERED' WHERE id = 'p1'")
        conn.commit()

    report = db_migration.verify_parity(sqlite_db, PG_DSN)
    assert not report.ok
    mism = [t.table for t in report.mismatches]
    assert "podcasts" in mism
    assert "followers" not in mism  # untouched table still matches


def test_embedded_nul_migrates_without_error_and_keeps_parity(tmp_path):
    """Defense-in-depth: SQLite TEXT can hold U+0000 but Postgres text cannot.
    A stray NUL must degrade to a 1-char loss (stripped on COPY), never abort
    the table, and the parity hash must strip identically so both sides match."""
    path = str(tmp_path / "nul.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, txt TEXT)")
    conn.execute("INSERT INTO t (txt) VALUES (?)", ("saut\x00 onions",))
    conn.execute("INSERT INTO t (txt) VALUES (?)", ("clean row",))
    conn.commit()
    conn.close()

    counts = db_migration.migrate_all(path, PG_DSN)
    assert counts == {"t": 2}

    report = db_migration.verify_parity(path, PG_DSN)
    assert report.ok, report.summary()

    import psycopg

    with psycopg.connect(PG_DSN) as pconn:
        val = pconn.execute("SELECT txt FROM sqlite_mirror.t WHERE txt LIKE 'saut%'").fetchone()[0]
    assert val == "saut onions"  # NUL dropped, rest intact


def test_parity_oracle_catches_row_count_drift(sqlite_db):
    import psycopg

    db_migration.migrate_all(sqlite_db, PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("DELETE FROM sqlite_mirror.followers WHERE tag = 'a'")
        conn.commit()

    report = db_migration.verify_parity(sqlite_db, PG_DSN)
    followers = next(t for t in report.tables if t.table == "followers")
    assert followers.sqlite_rows == 3
    assert followers.pg_rows == 2
    assert not followers.ok
