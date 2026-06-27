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

"""SQLite extension loaders.

Spec #28 §2.10 — the ``sqlite-vec`` extension is required for the
``vec0`` virtual table that backs corpus semantic search. It ships in
the ``[entities]`` optional extra; deployments that don't install
that extra have no chunk index and search code paths surface a typed
error to the caller.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union


class SqliteVecNotInstalledError(RuntimeError):
    """Raised when the ``sqlite-vec`` Python package is not importable.

    Migration code catches it and skips chunk DDL — the rest of the
    schema remains usable. Search and chunk-write code paths surface
    it to the caller so the user knows to install the ``[entities]``
    extra.
    """


def load_vec_extension(conn: sqlite3.Connection) -> None:
    """Load the ``sqlite-vec`` extension into ``conn``.

    Raises:
        SqliteVecNotInstalledError: when the ``sqlite-vec`` Python
            package is not installed in the current environment.
    """
    try:
        import sqlite_vec  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SqliteVecNotInstalledError(
            "sqlite-vec is not installed. Install thestill[entities] to " "enable corpus chunk search."
        ) from exc
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


_vec_available: bool | None = None


def maybe_load_vec_extension(conn: sqlite3.Connection) -> bool:
    """Best-effort load of ``sqlite-vec`` into ``conn``.

    Returns True if loaded, False if the package isn't installed.
    Repository ``_get_connection`` helpers call this so cascades from
    DELETE/UPDATE on ``episodes`` (which fan out via FK CASCADE into
    ``chunks`` and fire the AFTER triggers that touch ``chunks_vec``)
    don't crash. The ``False`` outcome is cached at module level so
    deployments without the ``[entities]`` extra don't pay the
    ``ImportError`` machinery on every connection open.
    """
    global _vec_available
    if _vec_available is False:
        return False
    try:
        load_vec_extension(conn)
    except SqliteVecNotInstalledError:
        _vec_available = False
        return False
    _vec_available = True
    return True


# Per-connection write-lock budget (milliseconds). A contended writer waits up
# to this long for the single SQLite writer lock before raising
# ``database is locked``. Without it the default is 0 — fail-fast on the first
# collision. See ``connect`` for the full concurrency rationale.
BUSY_TIMEOUT_MS = 5000


@contextmanager
def connect(
    db_path: Union[str, Path],
    *,
    load_vec: str = "none",
    row_factory: bool = True,
) -> Iterator[sqlite3.Connection]:
    """Open a tuned SQLite connection; commit on success, rollback on error, always close.

    Single source of truth for the per-connection PRAGMAs every repository
    and writer needs, so the concurrency story can't silently drift apart
    between connection sites (which is how ``database is locked`` storms
    creep in — one repo with ``busy_timeout`` set, another without):

    - ``foreign_keys = ON``    — off by default in SQLite.
    - ``journal_mode = WAL``   — one writer + many readers proceed
      concurrently instead of stalling behind a single journal lock. WAL
      is a persistent DB property, but re-asserting it per connection is
      cheap and keeps fresh DBs correct regardless of which repo opens first.
    - ``busy_timeout``         — a contended writer waits up to
      ``BUSY_TIMEOUT_MS`` for the writer lock and serializes gracefully,
      instead of fail-fast crashing the moment a peer holds it.
    - ``synchronous = NORMAL`` — safe under WAL and shortens how long a
      writer holds the lock by skipping the per-commit ``fsync`` that the
      default ``FULL`` forces.

    Args:
        db_path: Path to the SQLite database file.
        load_vec: sqlite-vec extension policy. ``"none"`` (default) skips it;
            ``"soft"`` best-effort loads it (repos whose cascades fire the
            ``chunks_vec`` triggers but stay usable without the extra);
            ``"require"`` hard-loads and raises ``SqliteVecNotInstalledError``
            if missing (writers that cannot function without vec0).
        row_factory: Use ``sqlite3.Row`` for dict-like column access.
    """
    conn = sqlite3.connect(str(db_path))
    if row_factory:
        conn.row_factory = sqlite3.Row
    if load_vec == "require":
        load_vec_extension(conn)
    elif load_vec == "soft":
        maybe_load_vec_extension(conn)
    elif load_vec != "none":
        raise ValueError(f"load_vec must be 'none', 'soft', or 'require', got {load_vec!r}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
