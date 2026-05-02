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
