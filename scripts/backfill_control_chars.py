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

"""One-time backfill: strip LLM-emitted control characters from stored text.

Companion to the ``CleanupPatch`` sanitizer (utils/text_sanitizer.py). That
validator stops NEW corruption at the clean stage; this script repairs what
already landed: Gemini occasionally replaced a non-ASCII character's UTF-8
bytes with control bytes (observed: ``Pok\\x00\\x00mon`` for Pokémon,
``saut\\x00`` for sauté, U+0003/U+0004 for £/ä), which SQLite stored silently.

Repairs, idempotently:

1. ``chunks.text`` and ``entity_mentions.quote_excerpt`` rows — plain UPDATEs;
   the ``chunks_au`` trigger fans the fix into ``chunks_fts``/``chunks_vec``.
2. On-disk cleaned-transcript artefacts: raw control bytes in ``.md`` files,
   and the corresponding ``\\uNNNN`` escape sequences in ``.json`` sidecars
   (surgical textual replacement — file structure untouched).

Usage::

    ./venv/bin/python scripts/backfill_control_chars.py            # dry-run
    ./venv/bin/python scripts/backfill_control_chars.py --apply
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thestill.utils.sqlite_ext import connect as sqlite_connect  # noqa: E402
from thestill.utils.sqlite_ext import maybe_load_vec_extension  # noqa: E402
from thestill.utils.text_sanitizer import _CONTROL_CHARS_RE, sanitize_text  # noqa: E402

DB_TARGETS = [
    ("chunks", "id", "text"),
    ("entity_mentions", "id", "quote_excerpt"),
    ("entity_mentions", "id", "surface_text"),
    ("entities", "id", "canonical_name"),
]

FILE_ROOTS = ["data/clean_transcripts", "data/summaries", "data/episode_facts", "data/podcast_facts", "data/digests"]

# JSON sidecars escape control chars as backslash-u sequences (six chars:
# backslash,u,0,0,0,0). Replace those escapes textually so the file's
# structure/formatting is untouched.
_JSON_ESCAPE_RE = re.compile(r"\\u00(0[0-8bcBC]|0[eE]|1[0-9a-fA-F]|7[fF]|8[0-9a-fA-F]|9[0-9a-fA-F])")


def backfill_db(db_path: str, apply: bool) -> int:
    # Must load sqlite-vec: the chunks_au UPDATE trigger fans into the
    # ``vec0`` virtual table, which only exists when the extension is loaded —
    # otherwise the UPDATE fails with "no such module: vec0".
    with sqlite_connect(Path(db_path)) as conn:
        maybe_load_vec_extension(conn)
        return _backfill_db_rows(conn, apply)


def _backfill_db_rows(conn: sqlite3.Connection, apply: bool) -> int:
    fixed = 0
    for table, pk, col in DB_TARGETS:
        dirty = []
        for rowid, val in conn.execute(f'SELECT "{pk}", "{col}" FROM "{table}"'):
            if isinstance(val, str):
                clean, removed = sanitize_text(val)
                if removed:
                    dirty.append((rowid, clean, removed))
        for rowid, clean, removed in dirty:
            print(f"  db: {table}.{col} id={rowid} strip {removed} char(s)")
            if apply:
                conn.execute(f'UPDATE "{table}" SET "{col}" = ? WHERE "{pk}" = ?', (clean, rowid))
            fixed += 1
    if apply:
        conn.commit()
    return fixed


def backfill_files(apply: bool) -> int:
    fixed = 0
    for root in FILE_ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".json"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if path.suffix == ".json":
                clean, n = _JSON_ESCAPE_RE.subn("", text)
                clean, n_raw = _CONTROL_CHARS_RE.subn("", clean)
                n += n_raw
            else:
                clean, n = _CONTROL_CHARS_RE.subn("", text)
            if n:
                print(f"  file: {path} strip {n} occurrence(s)")
                if apply:
                    path.write_text(clean, encoding="utf-8")
                fixed += 1
    return fixed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--db", default="data/podcasts.db")
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] scanning database …")
    rows = backfill_db(args.db, args.apply)
    print(f"[{mode}] scanning artefact files …")
    files = backfill_files(args.apply)
    print(f"[{mode}] {rows} db row(s), {files} file(s) {'fixed' if args.apply else 'would be fixed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
