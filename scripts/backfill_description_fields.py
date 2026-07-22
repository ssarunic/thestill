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

"""One-time backfill: repair inverted episode description fields.

Companion to the ``_extract_descriptions`` fix in core/media_source.py. That
fix picks the description variant by content for NEW episodes; this script
repairs what already landed: feeds like The Guardian's put real HTML in
``<description>`` and double-escaped tag remnants in ``<itunes:summary>``,
so we stored HTML in ``episodes.description`` (the plain-text column, fed to
LLM prompts) and escaped junk in ``episodes.description_html`` (rendered by
the web UI as literal ``<a href=...>`` text).

Only rows that exhibit the problem are touched — the plain column contains
real tags, or the HTML column contains escapes that decode into real markup
(a bare ``&lt;`` in legitimate prose does not qualify). Their
(description, description_html) pair is re-resolved with the same
``resolve_description_variants`` used at ingest. Idempotent: re-resolved
rows no longer match the selection predicate. On ``--apply`` the original
values of every changed row are saved to a JSON backup first.

Usage::

    ./venv/bin/python scripts/backfill_description_fields.py            # dry-run
    ./venv/bin/python scripts/backfill_description_fields.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from thestill.utils.html_utils import (  # noqa: E402
    count_html_tags,
    resolve_description_variants,
    unescape_entities_stable,
)

# SQL-side prefilter (broad, cheap): candidate rows have an angle bracket in
# the plain column or an escaped one in the HTML column. Python re-checks
# with count_html_tags so prose like "i<n" is never rewritten.
CANDIDATE_SQL = (
    "SELECT id, description, description_html FROM episodes "
    "WHERE description LIKE '%<%' OR description_html LIKE '%&lt;%'"
)
UPDATE_SQL_PG = "UPDATE episodes SET description = %s, description_html = %s WHERE id = %s"
UPDATE_SQL_SQLITE = "UPDATE episodes SET description = ?, description_html = ? WHERE id = ?"


def _has_double_escaped_markup(text: str) -> bool:
    """True when unescaping entities reveals tags that weren't visible before.

    A bare ``&lt;`` in otherwise-correct HTML (e.g. prose about the ``<``
    operator) must NOT count as a remnant — only escapes that decode into
    real markup do.
    """
    return count_html_tags(unescape_entities_stable(text)) > count_html_tags(text)


def needs_repair(description: str, description_html: str) -> bool:
    """True when the stored pair shows the inversion symptom."""
    return count_html_tags(description) > 0 or _has_double_escaped_markup(description_html)


def compute_fixes(rows: list[tuple]) -> list[tuple[str, str, str]]:
    """Return (new_plain, new_html, id) for every row that actually changes."""
    fixes = []
    for row_id, description, description_html in rows:
        description = description or ""
        description_html = description_html or ""
        if not needs_repair(description, description_html):
            continue
        new_plain, new_html = resolve_description_variants([description, description_html])
        if (new_plain, new_html) != (description, description_html):
            fixes.append((new_plain, new_html, str(row_id)))
    return fixes


def write_backup(rows_by_id: dict[str, tuple[str, str]], fixes: list[tuple[str, str, str]]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(f"data/backfill_description_fields_backup_{stamp}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = {
        row_id: {"description": rows_by_id[row_id][0], "description_html": rows_by_id[row_id][1]}
        for _, _, row_id in fixes
    }
    path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run(apply: bool, db_path: str) -> int:
    dsn = os.getenv("DATABASE_URL", "")
    if dsn:
        from thestill.utils.postgres_ext import connect as pg_connect

        conn = pg_connect(dsn)
        update_sql = UPDATE_SQL_PG
    else:
        from thestill.utils.sqlite_ext import connect as sqlite_connect

        conn = sqlite_connect(Path(db_path))
        update_sql = UPDATE_SQL_SQLITE

    with conn:
        fetched = conn.execute(CANDIDATE_SQL).fetchall()
        # postgres_ext.connect uses dict_row; sqlite returns tuples.
        rows = [(r["id"], r["description"], r["description_html"]) if isinstance(r, dict) else r for r in fetched]
        fixes = compute_fixes(rows)

        for new_plain, new_html, row_id in fixes:
            print(f"  episode {row_id}: plain {len(new_plain)} chars, html {len(new_html)} chars")

        if apply and fixes:
            rows_by_id = {str(r[0]): (r[1] or "", r[2] or "") for r in rows}
            backup_path = write_backup(rows_by_id, fixes)
            print(f"  originals backed up to {backup_path}")
            cur = conn.cursor()
            cur.executemany(update_sql, fixes)
    conn.close()
    return len(fixes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--db", default="data/podcasts.db", help="SQLite path when DATABASE_URL is unset")
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    backend = "postgres" if os.getenv("DATABASE_URL") else "sqlite"
    print(f"[{mode}] scanning episodes ({backend}) …")
    n = run(args.apply, args.db)
    print(f"[{mode}] {n} episode(s) {'repaired' if args.apply else 'would be repaired'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
