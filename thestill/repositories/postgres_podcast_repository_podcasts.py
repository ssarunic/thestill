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

"""PostgreSQL port of the PODCAST-side of ``SqlitePodcastRepository`` (spec #44).

``PodcastsMixin`` carries the podcast-facing methods of the repository,
ported faithfully from the SQLite implementation following the
``utils.postgres_ext`` conventions:

- ``%s`` placeholders (never ``?``).
- ``uuid`` columns: str params bind directly; reads come back as
  ``uuid.UUID`` and are stringified via ``as_str``.
- ``timestamptz`` columns: tz-aware ``datetime`` in, tz-aware out — no
  ``isoformat()``/``fromisoformat()`` round-trips (removes the SQLite
  text-timestamp foot-gun, spec #42 FM-3).
- ``boolean`` columns (``explicit``, ``is_complete``, ``synthetic``,
  ``auto_added``): native Python ``bool``, no 0/1 mapping.
- Upserts via ``INSERT ... ON CONFLICT``; generated ids via ``RETURNING``.
- ``ILIKE`` only for the user-facing chart search; exact-match ``LIKE``
  usages stay ``=``.

The mixin has NO ``__init__`` — the composing class sets ``self.dsn``.
All DDL lives in ``postgres_schema.py``; this module never emits schema.
The SQLite-only bootstrap (``_run_migrations`` / ``_create_schema`` /
``_seed_*`` / ``_backfill_*``) is intentionally NOT ported.

The category id<->(top, sub) resolution that SQLite loads eagerly in
``__init__`` is reimplemented here as a lazy per-instance cache filled from
the ``categories`` table on first use (no seeding: an empty table simply
resolves everything to ``None``).

NOTE: ``_episode_from_row`` duplicates the episode row mapping that the
episode-side mixin also carries — accepted for now, flagged for cleanup.
"""

from __future__ import annotations

import json
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import psycopg
from structlog import get_logger

from ..core.refresh_failure import (
    RefreshAction,
    RefreshDecision,
    RefreshFailure,
    RefreshPolicySettings,
    compute_backoff_interval,
    decide_refresh_action,
    resolve_streak_state,
)
from ..models.podcast import Episode, FailureType, Podcast
from ..utils.datetime_utils import ensure_utc, now_utc
from ..utils.podcast_categories import normalize_category_name
from ..utils.postgres_ext import as_str, connect
from ..utils.slug import generate_slug
from .sqlite_podcast_repository import SYNTHETIC_AUDIO_IMPORTS_ID, SYNTHETIC_AUDIO_IMPORTS_RSS, _normalize_artwork_url

logger = get_logger(__name__)

# Per-region chart JSONs (data/top_podcasts_<region>.json), produced by
# scripts/build_top_podcasts.py. Postgres seeds from the same files the SQLite
# repo globs (spec #57), so "drop a new JSON file" adds a region on either
# backend. ``_MTIME_EPSILON`` matches the SQLite gate so a file's float mtime
# compares equal across the two repos.
_TOP_PODCASTS_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_TOP_PODCASTS_GLOB = "top_podcasts_*.json"
_MTIME_EPSILON = 1e-6

# The canonical podcast projection used by every podcast SELECT below —
# identical column list to the SQLite queries.
_PODCAST_COLS = """id, created_at, rss_url, title, slug, description, image_url, language,
       primary_category_id, secondary_category_id,
       author, explicit, show_type, website_url, is_complete, copyright,
       last_processed, last_processed_at, etag, last_modified, updated_at"""

# Same projection with a ``p.`` table alias for JOIN queries.
_PODCAST_COLS_P = """p.id, p.created_at, p.rss_url, p.title, p.slug, p.description, p.image_url, p.language,
       p.primary_category_id, p.secondary_category_id,
       p.author, p.explicit, p.show_type, p.website_url, p.is_complete, p.copyright,
       p.last_processed, p.last_processed_at, p.etag, p.last_modified, p.updated_at"""


def _opt_bool(value: Any) -> Optional[bool]:
    """Nullable boolean read (native bool in PG; None stays None)."""
    return None if value is None else bool(value)


def _episode_from_row(row: dict) -> Episode:
    """Build an ``Episode`` from a Postgres dict row.

    PG analogue of ``sqlite_podcast_repository.episode_from_row``: uuid
    columns are stringified, timestamptz columns arrive as tz-aware
    ``datetime`` (no parsing), booleans are native. ``duration`` is a
    ``text`` column in the typed schema; pydantic coerces it back to int.

    Duplicated in the episode-side mixin — noted for cleanup (spec #44).
    """
    failure_type = None
    if row.get("failure_type"):
        try:
            failure_type = FailureType(row["failure_type"])
        except ValueError:
            logger.warning(f"Unknown failure_type '{row['failure_type']}' for episode {as_str(row['id'])}")

    return Episode(
        id=as_str(row["id"]),
        podcast_id=as_str(row["podcast_id"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        external_id=row["external_id"],
        title=row["title"],
        slug=row["slug"] or "",
        description=row["description"],
        description_html=row.get("description_html") or "",
        pub_date=row["pub_date"],
        audio_url=row["audio_url"],
        duration=row["duration"],
        image_url=row["image_url"],
        explicit=_opt_bool(row["explicit"]),
        episode_type=row["episode_type"],
        episode_number=row["episode_number"],
        season_number=row["season_number"],
        website_url=row["website_url"],
        audio_file_size=row["audio_file_size"],
        audio_mime_type=row["audio_mime_type"],
        audio_path=row["audio_path"],
        downsampled_audio_path=row["downsampled_audio_path"],
        raw_transcript_path=row["raw_transcript_path"],
        clean_transcript_path=row["clean_transcript_path"],
        clean_transcript_json_path=row.get("clean_transcript_json_path"),
        playback_time_offset_seconds=(
            row["playback_time_offset_seconds"] if row.get("playback_time_offset_seconds") is not None else 0.0
        ),
        summary_path=row["summary_path"],
        published_at=row.get("published_at"),
        failed_at_stage=row["failed_at_stage"],
        failure_reason=row["failure_reason"],
        failure_type=failure_type,
        failed_at=row["failed_at"],
    )


class PodcastsMixin:
    """Podcast-side methods of the Postgres podcast repository (spec #44).

    Composed with the episode-side mixin into the concrete repository; the
    composing class provides ``self.dsn``. Thread-safe via
    connection-per-operation (same story as the SQLite repo).
    """

    dsn: str

    # ------------------------------------------------------------------
    # Connections / transactions
    # ------------------------------------------------------------------

    @contextmanager
    def _get_connection(self) -> Iterator[psycopg.Connection]:
        """Per-operation psycopg connection with dict rows.

        ``with connect(dsn) as conn`` commits on clean exit and rolls back
        on exception — matching the SQLite helper's semantics.
        """
        with connect(self.dsn) as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """Explicit transaction context manager.

        psycopg3 opens an implicit transaction on first statement and the
        connection context manager commits/rolls back at exit, so this is
        the same one-connection-one-transaction shape as the SQLite
        ``BEGIN TRANSACTION`` version.
        """
        with self._get_connection() as conn:
            yield conn

    # ------------------------------------------------------------------
    # Category id <-> (top, sub) resolution — lazy per-instance cache
    # ------------------------------------------------------------------

    def _ensure_category_cache(self, conn: psycopg.Connection) -> None:
        """Load the categories table into per-instance dicts on first use.

        The taxonomy is small (~100 rows) and effectively read-only at
        runtime. Unlike the SQLite repo (which loads it in ``__init__``
        right after seeding), the mixin has no init hook, so the cache is
        filled lazily by the first resolution call. An empty table yields
        empty caches — every lookup then resolves to ``None`` gracefully.
        """
        if getattr(self, "_cat_cache_loaded", False):
            return
        rows = conn.execute("SELECT id, name, parent_id FROM categories ORDER BY parent_id IS NOT NULL, id").fetchall()
        cat_id_to_pair: Dict[int, Tuple[Optional[str], Optional[str]]] = {}
        cat_pair_to_id: Dict[Tuple[str, Optional[str]], int] = {}
        top_id_to_name: Dict[int, str] = {}
        for row in rows:
            if row["parent_id"] is None:
                top_id_to_name[row["id"]] = row["name"]
                cat_id_to_pair[row["id"]] = (row["name"], None)
                cat_pair_to_id[(normalize_category_name(row["name"]), None)] = row["id"]
            else:
                top_name = top_id_to_name.get(row["parent_id"])
                if top_name is None:
                    continue  # orphan subcategory — defensive, FK should prevent
                cat_id_to_pair[row["id"]] = (top_name, row["name"])
                cat_pair_to_id[(normalize_category_name(top_name), normalize_category_name(row["name"]))] = row["id"]
        self._cat_id_to_pair = cat_id_to_pair
        self._cat_pair_to_id = cat_pair_to_id
        self._cat_cache_loaded = True

    def _resolve_category_strings_to_id(
        self, top: Optional[str], sub: Optional[str], conn: Optional[psycopg.Connection] = None
    ) -> Optional[int]:
        """Return the most-specific category FK id matching the inputs.

        - (None, _) → None
        - (top, None) or (top, unknown_sub) → id of the top-level row, or
          None if the top-level itself doesn't match the taxonomy.
        - (top, sub) → id of the subcategory row if both match, else top id,
          else None. (Best-effort matching per Q4-iii.)
        """
        if not top:
            return None
        if not getattr(self, "_cat_cache_loaded", False):
            if conn is not None:
                self._ensure_category_cache(conn)
            else:
                with self._get_connection() as own_conn:
                    self._ensure_category_cache(own_conn)
        top_norm = normalize_category_name(top)
        top_id = self._cat_pair_to_id.get((top_norm, None))
        if top_id is None:
            return None
        if not sub:
            return top_id
        sub_id = self._cat_pair_to_id.get((top_norm, normalize_category_name(sub)))
        return sub_id if sub_id is not None else top_id

    def _resolve_category_id_to_pair(
        self, cat_id: Optional[int], conn: Optional[psycopg.Connection] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """Return (top_name, sub_name) for a category FK id; both None if unknown."""
        if cat_id is None:
            return (None, None)
        if not getattr(self, "_cat_cache_loaded", False):
            if conn is not None:
                self._ensure_category_cache(conn)
            else:
                with self._get_connection() as own_conn:
                    self._ensure_category_cache(own_conn)
        return self._cat_id_to_pair.get(cat_id, (None, None))

    # ------------------------------------------------------------------
    # Top-podcasts (chart) seeding
    # ------------------------------------------------------------------

    def _seed_top_podcasts(self) -> None:
        """Sync top_podcasts + top_podcast_rankings from per-region JSON.

        The Postgres parity for the SQLite ``_seed_top_podcasts`` (spec #57).
        Before this, Postgres only ever received chart rows via the one-shot
        ``db_promotion`` migration, so dropping a ``data/top_podcasts_<region>``
        JSON — the documented way to add a region — did nothing on a
        Postgres-backed server (FM-6 parallel-path drift). Now both backends
        discover regions by glob and mtime-gate each import against
        ``top_podcasts_meta``.

        Called once from ``PostgresPodcastRepository.__init__`` (the repo is a
        process-lifetime singleton). Steady state is a single ``meta`` read
        then skip. Fail-open throughout: a missing schema, a malformed file, or
        a per-region SQL error is logged and skipped without blocking startup
        or the other regions (FM-1).
        """
        json_files = sorted(_TOP_PODCASTS_DIR.glob(_TOP_PODCASTS_GLOB))
        if not json_files:
            return

        try:
            with self._get_connection() as conn:
                meta = {
                    row["region"]: row["source_mtime"]
                    for row in conn.execute("SELECT region, source_mtime FROM top_podcasts_meta").fetchall()
                }
        except psycopg.Error as exc:
            # Most likely the schema hasn't been migrated yet — nothing to seed.
            logger.warning("top-podcasts seed skipped (meta unreadable)", error=str(exc))
            return

        for path in json_files:
            region = path.stem.removeprefix("top_podcasts_").lower()
            if not region:
                continue
            mtime = path.stat().st_mtime
            if region in meta and abs(meta[region] - mtime) < _MTIME_EPSILON:
                continue  # unchanged — skip

            try:
                rows = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                logger.warning(
                    "skipping malformed top-podcasts file",
                    region=region,
                    path=str(path),
                    error=str(exc),
                )
                continue

            try:
                self._seed_top_podcasts_region(region, rows, path, mtime)
            except psycopg.Error as exc:
                logger.warning("top-podcasts region seed failed", region=region, error=str(exc))
                continue

    def _seed_top_podcasts_region(self, region: str, rows: List[Dict[str, Any]], path: Path, mtime: float) -> None:
        """Atomically reseed one region: drop its rankings, upsert podcasts, re-rank.

        Mirrors the SQLite path row-for-row (dedupe by ``rss_url`` keeping the
        best rank, upsert podcast metadata, insert one thin ranking row, bump
        the meta row) using the Postgres port conventions: ``%s`` placeholders,
        ``ON CONFLICT ... RETURNING``, native tz-aware ``timestamptz`` params.
        The whole region commits or rolls back as one transaction.
        """
        # Dedupe by rss_url within the region — Apple sometimes lists the same
        # canonical feed under two track_ids; keep the better (lower) rank.
        best_by_rss: Dict[str, Tuple[int, Dict[str, Any]]] = {}
        for i, row in enumerate(rows, start=1):
            rss_url = (row.get("rss_url") or "").strip()
            if not rss_url:
                continue
            rank = row.get("rank") or i
            existing = best_by_rss.get(rss_url)
            if existing is None or rank < existing[0]:
                best_by_rss[rss_url] = (rank, row)

        now = now_utc()
        with self._get_connection() as conn:
            self._ensure_category_cache(conn)
            conn.execute("DELETE FROM top_podcast_rankings WHERE region = %s", (region,))

            inserted = 0
            for rss_url, (rank, row) in best_by_rss.items():
                category_id = self._resolve_category_strings_to_id(row.get("category"), row.get("subcategory"), conn)
                pid = conn.execute(
                    """
                    INSERT INTO top_podcasts (
                        name, artist, rss_url, apple_url, youtube_url,
                        apple_track_id, image_url, category_id, first_seen_at, last_seen_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (rss_url) DO UPDATE SET
                        name = EXCLUDED.name,
                        artist = EXCLUDED.artist,
                        apple_url = EXCLUDED.apple_url,
                        youtube_url = EXCLUDED.youtube_url,
                        apple_track_id = EXCLUDED.apple_track_id,
                        image_url = EXCLUDED.image_url,
                        category_id = EXCLUDED.category_id,
                        last_seen_at = EXCLUDED.last_seen_at
                    RETURNING id
                    """,
                    (
                        row.get("name") or "",
                        row.get("artist"),
                        rss_url,
                        row.get("apple_url"),
                        row.get("youtube_url"),
                        row.get("track_id"),
                        _normalize_artwork_url(row.get("image_url")),
                        category_id,
                        now,
                        now,
                    ),
                ).fetchone()["id"]
                conn.execute(
                    """
                    INSERT INTO top_podcast_rankings (
                        top_podcast_id, region, rank, source_genre, scraped_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (pid, region, rank, row.get("source_genre"), now),
                )
                inserted += 1

            conn.execute(
                """
                INSERT INTO top_podcasts_meta (region, source_path, source_mtime, row_count, seeded_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (region) DO UPDATE SET
                    source_path = EXCLUDED.source_path,
                    source_mtime = EXCLUDED.source_mtime,
                    row_count = EXCLUDED.row_count,
                    seeded_at = EXCLUDED.seeded_at
                """,
                (region, str(path), mtime, inserted, now),
            )
        logger.info("seeded top-podcasts region", region=region, count=inserted)

    # ------------------------------------------------------------------
    # Refresh loaders (spec #19 / #48)
    # ------------------------------------------------------------------

    def get_podcasts_for_refresh(self) -> Tuple[List[Podcast], Dict[str, Set[str]]]:
        """Lightweight refresh loader (spec #19).

        Replaces ``get_all()`` on the refresh hot path. Two queries total:
        one for all podcasts (no episode hydration), one for every
        ``(podcast_id, external_id)`` pair used for in-memory dedup.

        Skips synthetic parents and any podcast no user follows (spec
        #63) — a feed with zero followers shouldn't drive recurring
        feed polls until someone subscribes.

        Returns:
            ``(podcasts, known_external_ids_by_podcast)`` where each
            ``Podcast`` has an empty ``episodes`` list and the dict maps
            ``podcast_id`` to the set of known ``external_id`` values.
            A podcast with no tracked episodes has no key in the dict.
        """
        with self._get_connection() as conn:
            podcast_rows = conn.execute(
                f"""
                SELECT {_PODCAST_COLS_P}
                FROM podcasts p
                WHERE {self._active_feed_sql("p", require_incomplete=False)}
                ORDER BY p.created_at DESC
                """
            ).fetchall()

            dedup: Dict[str, Set[str]] = {}
            for ext_row in conn.execute("SELECT podcast_id, external_id FROM episodes"):
                dedup.setdefault(as_str(ext_row["podcast_id"]), set()).add(ext_row["external_id"])

            podcasts = [self._row_to_podcast_no_episodes(row, conn) for row in podcast_rows]
        return podcasts, dedup

    def get_podcast_for_refresh(self, podcast_id: str) -> Optional[Tuple[Podcast, Set[str]]]:
        """Single-feed analogue of :meth:`get_podcasts_for_refresh` (spec #48).

        Loads one podcast (cache headers + watermark, episodes left empty)
        plus the set of its known ``external_id`` values. ``None`` if not
        found.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {_PODCAST_COLS} FROM podcasts WHERE id = %s",
                (podcast_id,),
            ).fetchone()
            if row is None:
                return None
            known = {
                r["external_id"]
                for r in conn.execute("SELECT external_id FROM episodes WHERE podcast_id = %s", (podcast_id,))
            }
            podcast = self._row_to_podcast_no_episodes(row, conn)
        return podcast, known

    def _row_to_podcast_no_episodes(self, row: dict, conn: psycopg.Connection) -> Podcast:
        """Shared refresh-loader row mapping (episodes deliberately empty)."""
        primary_top, primary_sub = self._resolve_category_id_to_pair(row["primary_category_id"], conn)
        secondary_top, secondary_sub = self._resolve_category_id_to_pair(row["secondary_category_id"], conn)
        return Podcast(
            id=as_str(row["id"]),
            created_at=row["created_at"],
            rss_url=row["rss_url"],
            title=row["title"],
            slug=row["slug"] or "",
            description=row["description"],
            image_url=row["image_url"],
            language=row["language"] if row["language"] else "en",
            primary_category=primary_top,
            primary_subcategory=primary_sub,
            secondary_category=secondary_top,
            secondary_subcategory=secondary_sub,
            author=row["author"],
            explicit=_opt_bool(row["explicit"]),
            show_type=row["show_type"],
            website_url=row["website_url"],
            is_complete=bool(row["is_complete"]) if row["is_complete"] is not None else False,
            copyright=row["copyright"],
            last_processed=row["last_processed"],
            last_processed_at=row.get("last_processed_at"),
            etag=row["etag"],
            last_modified=row["last_modified"],
            episodes=[],
        )

    # ------------------------------------------------------------------
    # Top-podcast (chart) lookups
    # ------------------------------------------------------------------

    def get_top_podcast_regions(self) -> List[str]:
        """Return the list of regions that currently have top-podcast data."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT region FROM top_podcasts_meta ORDER BY region").fetchall()
            return [row["region"] for row in rows]

    def get_top_podcast_categories(self, region: str) -> List[str]:
        """Return the distinct **top-level** category names in a region's chart.

        Chart entries can be tagged with either a top-level category (Comedy)
        or a sub-category (Comedy Interviews); sub-categories roll up to
        their parent so the UI matches Apple's primary category browser.

        Sorted alphabetically (case-insensitive — the ``COLLATE NOCASE``
        analogue), ``NULL``-categories suppressed, computed from the
        *unfiltered* ranking so the picker doesn't shrink under filters.
        """
        if not region:
            return []
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT name FROM (
                    SELECT DISTINCT COALESCE(parent.name, c.name) AS name
                    FROM top_podcast_rankings r
                    JOIN top_podcasts p ON p.id = r.top_podcast_id
                    JOIN categories c ON c.id = p.category_id
                    LEFT JOIN categories parent ON parent.id = c.parent_id
                    WHERE r.region = %s AND COALESCE(parent.name, c.name) IS NOT NULL
                ) names
                ORDER BY LOWER(name), name
                """,
                (region.lower(),),
            ).fetchall()
            return [row["name"] for row in rows]

    def is_top_podcast_in_region(self, rss_url: str, region: str) -> bool:
        """Return True if the given RSS URL is in the top chart for ``region``.

        Used by the free-tier subscription gate: non-paying users may only
        subscribe to podcasts that appear on their region's top chart.
        """
        if not rss_url or not region:
            return False
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM top_podcast_rankings r
                JOIN top_podcasts p ON p.id = r.top_podcast_id
                WHERE r.region = %s AND p.rss_url = %s
                LIMIT 1
                """,
                (region.lower(), rss_url),
            ).fetchone()
        return row is not None

    def get_top_podcasts(
        self,
        region: str,
        *,
        limit: int = 500,
        category: Optional[str] = None,
        q: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List the top chart for a region, optionally filtered by category and/or query.

        Returns plain dicts (not Podcast models) since chart entries may not
        correspond to a subscribed ``podcasts`` row.

        ``q`` is a case-insensitive substring matched against ``name`` and
        ``artist`` (user-facing search → ``ILIKE`` per the port
        conventions). Rank order is preserved.

        ``user_id`` enables the ``is_following`` flag per row; ``None``
        (anonymous) makes every row report ``is_following=False`` because
        the ``LEFT JOIN`` simply misses. ``podcast_slug`` rides the same
        ``podcasts`` join; ``None`` for unimported entries. ``image_url``
        prefers the imported podcast's artwork and falls back to the chart's
        own ``top_podcasts.image_url`` (Apple ``artworkUrl600`` at scrape
        time), so unimported entries still render a cover.
        """
        if not region:
            return []

        # `user_id` is bound first because the LEFT JOIN's `pf.user_id = %s`
        # appears in the FROM clause, before WHERE-clause params.
        params: List[Any] = [user_id, region.lower()]
        category_filter = ""
        if category:
            # The UI picker shows top-level Apple categories, but chart
            # entries can be tagged with either a top-level or sub-category
            # (e.g. "Comedy Interviews" under "Comedy"). Match both sides.
            category_filter = " AND (c.name = %s OR cat_parent.name = %s)"
            params.extend([category, category])

        query_filter = ""
        if q:
            query_filter = " AND (p.name ILIKE %s OR p.artist ILIKE %s)"
            like = f"%{q}%"
            params.extend([like, like])

        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT r.rank, p.name, p.artist, p.rss_url, p.apple_url, p.youtube_url,
                       c.name AS category, r.source_genre,
                       up.slug AS podcast_slug,
                       COALESCE(up.image_url, p.image_url) AS image_url,
                       CASE WHEN pf.user_id IS NOT NULL THEN 1 ELSE 0 END AS is_following
                FROM top_podcast_rankings r
                JOIN top_podcasts p ON p.id = r.top_podcast_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN categories cat_parent ON cat_parent.id = c.parent_id
                LEFT JOIN podcasts up ON up.rss_url = p.rss_url
                LEFT JOIN podcast_followers pf
                       ON pf.podcast_id = up.id AND pf.user_id = %s
                WHERE r.region = %s{category_filter}{query_filter}
                ORDER BY r.rank ASC
                LIMIT %s
                """,
                params,
            ).fetchall()

        return [{**dict(row), "is_following": bool(row["is_following"])} for row in rows]

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def get_chunks_health(self) -> Tuple[int, str]:
        """Spec #28 §2.10 — chunk row count + dominant embedding model.

        Returns ``(0, "")`` when the ``chunks`` table doesn't exist or is
        empty (the typed schema always creates it, but the guard mirrors
        the SQLite defensiveness for partial deployments).
        """
        with self._get_connection() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n, "
                    "(SELECT embedding_model FROM chunks GROUP BY embedding_model "
                    "ORDER BY COUNT(*) DESC LIMIT 1) AS model "
                    "FROM chunks"
                ).fetchone()
            except psycopg.errors.UndefinedTable:
                return 0, ""
        if row is None:
            return 0, ""
        return int(row["n"] or 0), row["model"] or ""

    # ------------------------------------------------------------------
    # PodcastRepository interface — reads
    # ------------------------------------------------------------------

    def get_all(self) -> List[Podcast]:
        """Retrieve all podcasts with their episodes."""
        with self._get_connection() as conn:
            rows = conn.execute(f"SELECT {_PODCAST_COLS} FROM podcasts ORDER BY created_at DESC").fetchall()
            return [self._row_to_podcast(row, conn) for row in rows]

    def get(self, podcast_id: str) -> Optional[Podcast]:
        """Get podcast by internal UUID (primary key)."""
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {_PODCAST_COLS} FROM podcasts WHERE id = %s",
                (podcast_id,),
            ).fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def get_by_id(self, podcast_id: str) -> Optional[Podcast]:
        """Find podcast by internal UUID."""
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {_PODCAST_COLS} FROM podcasts WHERE id = %s",
                (podcast_id,),
            ).fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def get_by_url(self, url: str) -> Optional[Podcast]:
        """Find podcast by RSS URL."""
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {_PODCAST_COLS} FROM podcasts WHERE rss_url = %s",
                (url,),
            ).fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def get_by_index(self, index: int) -> Optional[Podcast]:
        """Find podcast by 1-based index."""
        if index < 1:  # Invalid index (must be 1-based)
            return None

        with self._get_connection() as conn:
            row = conn.execute(
                f"""
                SELECT {_PODCAST_COLS}
                FROM podcasts
                ORDER BY created_at DESC
                LIMIT 1 OFFSET %s
                """,
                (index - 1,),
            ).fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def get_by_slug(self, slug: str) -> Optional[Podcast]:
        """Find podcast by URL-safe slug."""
        if not slug:
            return None

        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {_PODCAST_COLS} FROM podcasts WHERE slug = %s",
                (slug,),
            ).fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    def exists(self, url: str) -> bool:
        """Check if podcast exists."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM podcasts WHERE rss_url = %s LIMIT 1",
                (url,),
            ).fetchone()
            return row is not None

    # ------------------------------------------------------------------
    # PodcastRepository interface — writes
    # ------------------------------------------------------------------

    def save(self, podcast: Podcast) -> Podcast:
        """
        Save or update podcast with ALL episodes (destructive).

        WARNING: This method DELETES all existing episodes and re-inserts them.
        Use save_podcast() + save_episode()/save_episodes() for targeted updates.

        Strategy: UPSERT podcast, then DELETE + INSERT all episodes
        Side effects: updated_at set on podcast and ALL episodes
        """
        with self._get_connection() as conn:
            now = datetime.now(timezone.utc)

            # Resolve string categories on the model into FK ids before write.
            primary_cat_id = self._resolve_category_strings_to_id(
                podcast.primary_category, podcast.primary_subcategory, conn
            )
            secondary_cat_id = self._resolve_category_strings_to_id(
                podcast.secondary_category, podcast.secondary_subcategory, conn
            )

            # Upsert podcast
            conn.execute(
                """
                INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug, description, image_url, language,
                                      primary_category_id, secondary_category_id,
                                      author, explicit, show_type, website_url, is_complete, copyright, last_processed)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (rss_url) DO UPDATE SET
                    title = EXCLUDED.title,
                    slug = EXCLUDED.slug,
                    description = EXCLUDED.description,
                    image_url = EXCLUDED.image_url,
                    language = EXCLUDED.language,
                    primary_category_id = EXCLUDED.primary_category_id,
                    secondary_category_id = EXCLUDED.secondary_category_id,
                    author = EXCLUDED.author,
                    explicit = EXCLUDED.explicit,
                    show_type = EXCLUDED.show_type,
                    website_url = EXCLUDED.website_url,
                    is_complete = EXCLUDED.is_complete,
                    copyright = EXCLUDED.copyright,
                    last_processed = EXCLUDED.last_processed,
                    updated_at = %s
                """,
                (
                    podcast.id,
                    podcast.created_at,
                    now,
                    str(podcast.rss_url),
                    podcast.title,
                    podcast.slug,
                    podcast.description,
                    _normalize_artwork_url(podcast.image_url),
                    podcast.language,
                    primary_cat_id,
                    secondary_cat_id,
                    podcast.author,
                    podcast.explicit,
                    podcast.show_type,
                    podcast.website_url,
                    podcast.is_complete,
                    podcast.copyright,
                    podcast.last_processed,
                    now,  # Set updated_at explicitly (no trigger)
                ),
            )

            # Get final podcast_id (in case URL already existed)
            row = conn.execute("SELECT id FROM podcasts WHERE rss_url = %s", (str(podcast.rss_url),)).fetchone()
            podcast_id = as_str(row["id"])

            # Delete existing episodes (simpler than complex merge logic)
            # Note: No CASCADE - we explicitly delete here
            conn.execute("DELETE FROM episodes WHERE podcast_id = %s", (podcast_id,))

            # Insert all episodes
            for episode in podcast.episodes:
                self._save_episode_row(conn, podcast_id, episode, now)

            logger.debug(f"Saved podcast: {podcast.title} ({len(podcast.episodes)} episodes)")
            return podcast

    def _save_episode_row(self, conn: psycopg.Connection, podcast_id: str, episode: Episode, now: datetime) -> None:
        """Insert episode into database (PG port of ``_save_episode``).

        ``duration`` is a ``text`` column in the typed schema, so the int
        model field is stringified on write (pydantic coerces it back on
        read).
        """
        conn.execute(
            """
            INSERT INTO episodes (
                id, podcast_id, created_at, updated_at, external_id, title, slug, description,
                description_html, pub_date, audio_url, duration, image_url,
                explicit, episode_type, episode_number, season_number, website_url,
                audio_file_size, audio_mime_type,
                audio_path, downsampled_audio_path, raw_transcript_path, clean_transcript_path,
                clean_transcript_json_path, summary_path, playback_time_offset_seconds,
                failed_at_stage, failure_reason, failure_type, failed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                episode.id,
                podcast_id,
                episode.created_at,
                now,
                episode.external_id,
                episode.title,
                episode.slug,
                episode.description,
                episode.description_html,
                episode.pub_date,
                str(episode.audio_url),
                str(episode.duration) if episode.duration is not None else None,
                _normalize_artwork_url(episode.image_url),
                episode.explicit,
                episode.episode_type,
                episode.episode_number,
                episode.season_number,
                episode.website_url,
                episode.audio_file_size,
                episode.audio_mime_type,
                episode.audio_path,
                episode.downsampled_audio_path,
                episode.raw_transcript_path,
                episode.clean_transcript_path,
                episode.clean_transcript_json_path,
                episode.summary_path,
                episode.playback_time_offset_seconds,
                episode.failed_at_stage,
                episode.failure_reason,
                episode.failure_type.value if episode.failure_type else None,
                episode.failed_at,
            ),
        )

    def touch_last_processed_at(self, podcast_id: str, when: datetime) -> None:
        """Record the wall-clock time an episode was last processed.

        Targeted single-column UPDATE so it can never clobber the discovery
        watermark (``last_processed``) — keeping the two semantics fully
        separate.
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE podcasts SET last_processed_at = %s WHERE id = %s",
                (when, podcast_id),
            )

    def save_podcast(self, podcast: Podcast) -> Podcast:
        """
        Save or update podcast metadata only. Does NOT touch episodes.

        Idempotent: Only updates updated_at if data actually changed.

        Args:
            podcast: Podcast model with metadata to save

        Returns:
            The saved podcast (with updated timestamps if changed)
        """
        with self._get_connection() as conn:
            now = datetime.now(timezone.utc)

            # Check if podcast exists and if data changed
            existing = conn.execute(
                """
                SELECT id, title, slug, description, image_url, language,
                       primary_category_id, secondary_category_id,
                       author, explicit, show_type, website_url, is_complete, copyright,
                       last_processed, etag, last_modified
                FROM podcasts WHERE rss_url = %s
                """,
                (str(podcast.rss_url),),
            ).fetchone()

            primary_cat_id = self._resolve_category_strings_to_id(
                podcast.primary_category, podcast.primary_subcategory, conn
            )
            secondary_cat_id = self._resolve_category_strings_to_id(
                podcast.secondary_category, podcast.secondary_subcategory, conn
            )

            if existing:
                # Compare fields to see if anything changed. timestamptz and
                # boolean columns compare natively (no isoformat/0-1 mapping).
                normalized_image_url = _normalize_artwork_url(podcast.image_url)

                changed = (
                    existing["title"] != podcast.title
                    or existing["slug"] != podcast.slug
                    or existing["description"] != podcast.description
                    or existing["image_url"] != normalized_image_url
                    or existing["language"] != podcast.language
                    or existing["primary_category_id"] != primary_cat_id
                    or existing["secondary_category_id"] != secondary_cat_id
                    or existing["author"] != podcast.author
                    or existing["explicit"] != podcast.explicit
                    or existing["show_type"] != podcast.show_type
                    or existing["website_url"] != podcast.website_url
                    or existing["is_complete"] != podcast.is_complete
                    or existing["copyright"] != podcast.copyright
                    or existing["last_processed"] != podcast.last_processed
                    or existing["etag"] != podcast.etag
                    or existing["last_modified"] != podcast.last_modified
                )

                if changed:
                    # Update with new updated_at
                    conn.execute(
                        """
                        UPDATE podcasts
                        SET title = %s, slug = %s, description = %s, image_url = %s, language = %s,
                            primary_category_id = %s, secondary_category_id = %s,
                            author = %s, explicit = %s, show_type = %s, website_url = %s, is_complete = %s, copyright = %s,
                            last_processed = %s, etag = %s, last_modified = %s, updated_at = %s
                        WHERE rss_url = %s
                        """,
                        (
                            podcast.title,
                            podcast.slug,
                            podcast.description,
                            normalized_image_url,
                            podcast.language,
                            primary_cat_id,
                            secondary_cat_id,
                            podcast.author,
                            podcast.explicit,
                            podcast.show_type,
                            podcast.website_url,
                            podcast.is_complete,
                            podcast.copyright,
                            podcast.last_processed,
                            podcast.etag,
                            podcast.last_modified,
                            now,
                            str(podcast.rss_url),
                        ),
                    )
                    logger.debug(f"Updated podcast metadata: {podcast.title}")
                else:
                    logger.debug(f"Podcast metadata unchanged: {podcast.title}")
            else:
                # Insert new podcast
                conn.execute(
                    """
                    INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug, description, image_url, language,
                                          primary_category_id, secondary_category_id,
                                          author, explicit, show_type, website_url, is_complete, copyright,
                                          last_processed, etag, last_modified)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        podcast.id,
                        podcast.created_at,
                        now,
                        str(podcast.rss_url),
                        podcast.title,
                        podcast.slug,
                        podcast.description,
                        _normalize_artwork_url(podcast.image_url),
                        podcast.language,
                        primary_cat_id,
                        secondary_cat_id,
                        podcast.author,
                        podcast.explicit,
                        podcast.show_type,
                        podcast.website_url,
                        podcast.is_complete,
                        podcast.copyright,
                        podcast.last_processed,
                        podcast.etag,
                        podcast.last_modified,
                    ),
                )
                logger.debug(f"Inserted new podcast: {podcast.title}")

            return podcast

    def delete(self, url: str) -> bool:
        """
        Delete podcast by URL.

        Note: Episodes must be deleted first (no CASCADE).
        This is intentional for cache invalidation control.
        """
        with self._get_connection() as conn:
            # First, get podcast ID
            row = conn.execute("SELECT id FROM podcasts WHERE rss_url = %s", (url,)).fetchone()
            if not row:
                return False

            podcast_id = as_str(row["id"])

            # Explicitly delete episodes (for cache invalidation tracking)
            conn.execute("DELETE FROM episodes WHERE podcast_id = %s", (podcast_id,))

            # Then delete podcast
            conn.execute("DELETE FROM podcasts WHERE id = %s", (podcast_id,))

            logger.info(f"Deleted podcast: {url}")
            return True

    # ------------------------------------------------------------------
    # Spec #48 — background refresh scheduling (cadence + failure state)
    # ------------------------------------------------------------------

    def get_due_podcasts(self, now: Optional[datetime] = None, limit: int = 500) -> List[str]:
        """Return ids of feeds DUE for refresh, oldest-due first.

        Due = scheduled (``next_refresh_at IS NOT NULL`` — parked feeds are
        excluded) and ``next_refresh_at <= now``, restricted to active feeds
        (non-synthetic, ongoing).
        """
        now_dt = now or now_utc()
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id FROM podcasts
                WHERE next_refresh_at IS NOT NULL
                  AND next_refresh_at <= %s
                  AND (refresh_retry_after_at IS NULL OR refresh_retry_after_at <= %s)
                  AND {self._active_feed_sql()}
                ORDER BY next_refresh_at ASC
                LIMIT %s
                """,
                (now_dt, now_dt, limit),
            ).fetchall()
            return [as_str(row["id"]) for row in rows]

    @staticmethod
    def _active_feed_sql(alias: str = "podcasts", *, require_incomplete: bool = True) -> str:
        """Spec #63 — the single source of truth for refresh eligibility.

        Faithful port of the SQLite implementation; see there for the
        full rationale. A feed only receives recurring background work
        while at least one user follows it; explicit single-podcast
        operations bypass this predicate by design.
        """
        incomplete = f"AND COALESCE({alias}.is_complete, false) = false " if require_incomplete else ""
        return (
            f"COALESCE({alias}.synthetic, false) = false "
            f"{incomplete}"
            f"AND EXISTS (SELECT 1 FROM podcast_followers pf WHERE pf.podcast_id = {alias}.id)"
        )

    def get_quarantine_probe_due(
        self,
        probe_interval_seconds: int,
        now: Optional[datetime] = None,
        limit: int = 200,
    ) -> List[str]:
        """Spec #60 — quarantined feeds due one low-frequency re-probe.

        Only self-healable reasons (``feed_gone`` / ``invalid_content``) are
        probed; ``auth_required`` / ``blocked_unsafe`` need a human and are
        excluded STRUCTURALLY (SQL literal, no config path can widen it).
        """
        now_dt = now or now_utc()
        cutoff = now_dt - timedelta(seconds=probe_interval_seconds)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id FROM podcasts
                WHERE next_refresh_at IS NULL
                  AND refresh_disabled_reason IN ('feed_gone', 'invalid_content')
                  AND (last_refresh_at IS NULL OR last_refresh_at <= %s)
                  AND {self._active_feed_sql()}
                ORDER BY last_refresh_at ASC
                LIMIT %s
                """,
                (cutoff, limit),
            ).fetchall()
            return [as_str(row["id"]) for row in rows]

    def get_refresh_health_counts(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Spec #60 — one cheap aggregate for status surfacing (port of the
        SQLite implementation; see there for field semantics)."""
        now_dt = now or now_utc()
        active_filter = self._active_feed_sql()
        with self._get_connection() as conn:
            reason_rows = conn.execute(
                f"""
                SELECT COALESCE(refresh_disabled_reason, 'unknown') AS reason, COUNT(*) AS n
                FROM podcasts
                WHERE next_refresh_at IS NULL
                  AND (last_refresh_at IS NOT NULL OR last_refresh_error IS NOT NULL)
                  AND {active_filter}
                GROUP BY reason
                """
            ).fetchall()
            parked_by_reason = {row["reason"]: row["n"] for row in reason_rows}
            active = conn.execute(
                f"SELECT COUNT(*) AS n FROM podcasts WHERE next_refresh_at IS NOT NULL AND {active_filter}"
            ).fetchone()["n"]
            due_now = conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM podcasts
                WHERE next_refresh_at IS NOT NULL AND next_refresh_at <= %s AND {active_filter}
                """,
                (now_dt,),
            ).fetchone()["n"]
            backing_off = conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM podcasts
                WHERE next_refresh_at IS NOT NULL AND last_refresh_failure_kind IS NOT NULL
                  AND {active_filter}
                """
            ).fetchone()["n"]
        return {
            "active": active,
            "due_now": due_now,
            "backing_off": backing_off,
            "parked_total": sum(parked_by_reason.values()),
            "parked_by_reason": parked_by_reason,
        }

    def seed_unscheduled_feeds(self, default_interval_seconds: int, now: Optional[datetime] = None) -> int:
        """Seed active feeds that have NEVER been scheduled or attempted.

        Distinguishes never-seeded (``next_refresh_at`` NULL *and* no prior
        attempt) from PARKED (terminally failed): only the former is seeded,
        so a parked feed is never silently revived. Returns the number seeded.
        """
        now_dt = now or now_utc()
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id FROM podcasts
                WHERE next_refresh_at IS NULL
                  AND last_refresh_at IS NULL
                  AND last_refresh_error IS NULL
                  AND {self._active_feed_sql()}
                """
            ).fetchall()
            for row in rows:
                pid = as_str(row["id"])
                offset = (hash(pid) % max(1, default_interval_seconds)) if default_interval_seconds > 0 else 0
                next_at = now_dt + timedelta(seconds=offset)
                conn.execute(
                    "UPDATE podcasts SET refresh_interval_seconds = %s, next_refresh_at = %s WHERE id = %s",
                    (default_interval_seconds, next_at, pid),
                )
            if rows:
                logger.info("Seeded refresh schedule for unscheduled feeds", count=len(rows))
            return len(rows)

    def record_refresh_success(
        self,
        podcast_id: str,
        found_new: bool,
        min_interval: int,
        max_interval: int,
        default_interval: int,
        now: Optional[datetime] = None,
    ) -> str:
        """Record a successful refresh and recompute the adaptive (AIMD)
        interval. New episodes → shorten (÷2, decrease); none → lengthen
        (×1.5, increase). Clears ``last_refresh_error``. Returns the new
        ``next_refresh_at`` ISO string.
        """
        now_dt = now or now_utc()
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT refresh_interval_seconds FROM podcasts WHERE id = %s",
                (podcast_id,),
            ).fetchone()
            current = row["refresh_interval_seconds"] if row and row["refresh_interval_seconds"] else default_interval
            if found_new:
                new_interval = max(min_interval, current // 2)
            else:
                new_interval = min(max_interval, int(current * 1.5))
            new_interval = max(min_interval, min(max_interval, new_interval))
            next_at_dt = now_dt + timedelta(seconds=new_interval)
            conn.execute(
                """
                UPDATE podcasts
                SET refresh_interval_seconds = %s,
                    next_refresh_at = %s,
                    last_refresh_at = %s,
                    last_refresh_error = NULL,
                    last_refresh_failure_kind = NULL,
                    last_refresh_status_code = NULL,
                    consecutive_refresh_failures = 0,
                    refresh_failure_streak_started_at = NULL,
                    refresh_disabled_reason = NULL,
                    refresh_retry_after_at = NULL,
                    updated_at = %s
                WHERE id = %s
                """,
                (new_interval, next_at_dt, now_dt, now_dt, podcast_id),
            )
            return next_at_dt.isoformat()

    def record_refresh_failure(
        self,
        podcast_id: str,
        failure: RefreshFailure,
        settings: RefreshPolicySettings,
        now: Optional[datetime] = None,
    ) -> RefreshDecision:
        """Apply the spec #60 failure policy in ONE atomic state transition.

        Faithful port of the SQLite implementation (see there for the policy
        semantics): IGNORE stamps visibility only, BACKOFF lengthens and
        stays scheduled (never parks — the 2026-07-15 incident fix),
        QUARANTINE requires a decisive signal or an exhausted horizon. A
        kind change restarts the failure streak.
        """
        now_dt = now or now_utc()
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT refresh_interval_seconds, consecutive_refresh_failures,
                       refresh_failure_streak_started_at, last_refresh_failure_kind
                FROM podcasts WHERE id = %s
                """,
                (podcast_id,),
            ).fetchone()
            current_interval = (
                row["refresh_interval_seconds"]
                if row and row["refresh_interval_seconds"]
                else settings.default_interval_seconds
            )
            prior_consecutive = (row["consecutive_refresh_failures"] or 0) if row else 0
            prior_kind = row["last_refresh_failure_kind"] if row else None
            streak_started = ensure_utc(row["refresh_failure_streak_started_at"]) if row else None
            streak_for_decision, consecutive_before = resolve_streak_state(
                prior_kind, prior_consecutive, streak_started, failure.kind, now_dt
            )

            decision = decide_refresh_action(
                failure.kind,
                failure.http_status,
                current_interval_seconds=current_interval,
                streak_started_at=streak_for_decision,
                now=now_dt,
                settings=settings,
            )

            error_text = (failure.exception or "refresh failed")[:2000]
            if decision.action is RefreshAction.IGNORE:
                conn.execute(
                    """
                    UPDATE podcasts
                    SET last_refresh_at = %s, last_refresh_error = %s,
                        last_refresh_failure_kind = %s, last_refresh_status_code = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (now_dt, error_text, failure.kind.value, failure.http_status, now_dt, podcast_id),
                )
                return decision

            new_consecutive = consecutive_before + 1
            if decision.action is RefreshAction.QUARANTINE:
                conn.execute(
                    """
                    UPDATE podcasts
                    SET last_refresh_at = %s, last_refresh_error = %s,
                        last_refresh_failure_kind = %s, last_refresh_status_code = %s,
                        consecutive_refresh_failures = %s, refresh_failure_streak_started_at = %s,
                        refresh_disabled_reason = %s, next_refresh_at = NULL,
                        refresh_retry_after_at = NULL, updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        now_dt,
                        error_text,
                        failure.kind.value,
                        failure.http_status,
                        new_consecutive,
                        streak_for_decision,
                        decision.disabled_reason,
                        now_dt,
                        podcast_id,
                    ),
                )
                return decision

            new_interval = compute_backoff_interval(current_interval, settings)
            next_at_dt = now_dt + timedelta(seconds=new_interval)
            retry_after = ensure_utc(failure.retry_after)
            if retry_after is not None and retry_after > next_at_dt:
                next_at_dt = retry_after
            conn.execute(
                """
                UPDATE podcasts
                SET refresh_interval_seconds = %s, next_refresh_at = %s,
                    refresh_retry_after_at = %s, last_refresh_at = %s, last_refresh_error = %s,
                    last_refresh_failure_kind = %s, last_refresh_status_code = %s,
                    consecutive_refresh_failures = %s, refresh_failure_streak_started_at = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    new_interval,
                    next_at_dt,
                    retry_after,
                    now_dt,
                    error_text,
                    failure.kind.value,
                    failure.http_status,
                    new_consecutive,
                    streak_for_decision,
                    now_dt,
                    podcast_id,
                ),
            )
            return decision

    def clear_podcast_refresh_failure(
        self,
        podcast_id: str,
        default_interval: int,
        now: Optional[datetime] = None,
    ) -> str:
        """Operator retry of a parked/quarantined feed: clear all failure
        state and re-arm ``next_refresh_at`` to ``now`` so the next tick
        re-enqueues it. Returns the new ``next_refresh_at``.
        """
        now_dt = now or now_utc()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE podcasts
                SET last_refresh_error = NULL,
                    last_refresh_failure_kind = NULL,
                    last_refresh_status_code = NULL,
                    consecutive_refresh_failures = 0,
                    refresh_failure_streak_started_at = NULL,
                    refresh_disabled_reason = NULL,
                    refresh_retry_after_at = NULL,
                    next_refresh_at = %s,
                    refresh_interval_seconds = COALESCE(refresh_interval_seconds, %s),
                    updated_at = %s
                WHERE id = %s
                """,
                (now_dt, default_interval, now_dt, podcast_id),
            )
        return now_dt.isoformat()

    def clear_podcast_refresh_failures(
        self,
        podcast_ids: Sequence[str],
        default_interval: int,
        now: Optional[datetime] = None,
    ) -> int:
        """Bulk variant of :meth:`clear_podcast_refresh_failure` (spec #60
        Phase 0 — previously MISSING on Postgres, which broke the DLQ
        "retry all" endpoint on the live DB). One ``= ANY(%s)`` update; no
        chunking needed (Postgres has no SQLite-style parameter ceiling).
        Returns the number of rows updated.
        """
        ids = list(dict.fromkeys(podcast_ids))  # de-dupe, preserve order
        if not ids:
            return 0
        now_dt = now or now_utc()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE podcasts
                SET last_refresh_error = NULL,
                    last_refresh_failure_kind = NULL,
                    last_refresh_status_code = NULL,
                    consecutive_refresh_failures = 0,
                    refresh_failure_streak_started_at = NULL,
                    refresh_disabled_reason = NULL,
                    refresh_retry_after_at = NULL,
                    next_refresh_at = %s,
                    refresh_interval_seconds = COALESCE(refresh_interval_seconds, %s),
                    updated_at = %s
                WHERE id = ANY(%s)
                """,
                (now_dt, default_interval, now_dt, ids),
            )
            updated = cursor.rowcount or 0
        logger.info("Bulk-cleared podcast refresh failures", requested=len(ids), updated=updated)
        return updated

    # ------------------------------------------------------------------
    # Row mapping helpers
    # ------------------------------------------------------------------

    def _row_to_podcast(self, row: dict, conn: psycopg.Connection) -> Podcast:
        """Convert database row to Podcast model with episodes."""
        try:
            # Fetch episodes for this podcast. SQLite's ``ORDER BY pub_date
            # DESC`` places NULLs last; Postgres defaults NULLS FIRST on
            # DESC, so make the SQLite ordering explicit.
            ep_rows = conn.execute(
                "SELECT * FROM episodes WHERE podcast_id = %s ORDER BY pub_date DESC NULLS LAST",
                (as_str(row["id"]),),
            ).fetchall()
            episodes = [_episode_from_row(ep_row) for ep_row in ep_rows]

            primary_top, primary_sub = self._resolve_category_id_to_pair(row["primary_category_id"], conn)
            secondary_top, secondary_sub = self._resolve_category_id_to_pair(row["secondary_category_id"], conn)

            return Podcast(
                id=as_str(row["id"]),
                created_at=row["created_at"],
                rss_url=row["rss_url"],
                title=row["title"],
                slug=row["slug"] or "",
                description=row["description"],
                image_url=row["image_url"],
                language=row["language"] if row["language"] else "en",
                primary_category=primary_top,
                primary_subcategory=primary_sub,
                secondary_category=secondary_top,
                secondary_subcategory=secondary_sub,
                # THES-142: New fields
                author=row["author"],
                explicit=_opt_bool(row["explicit"]),
                show_type=row["show_type"],
                website_url=row["website_url"],
                is_complete=bool(row["is_complete"]) if row["is_complete"] is not None else False,
                copyright=row["copyright"],
                last_processed=row["last_processed"],
                last_processed_at=row.get("last_processed_at"),
                etag=row["etag"],
                last_modified=row["last_modified"],
                episodes=episodes,
            )
        except Exception as e:
            logger.error(f"Error in _row_to_podcast: {e}", exc_info=True)
            raise

    def _row_to_podcast_minimal(self, row: dict) -> Podcast:
        """Convert a ``p_``-aliased JOIN row to a Podcast model without episodes."""
        primary_top, primary_sub = self._resolve_category_id_to_pair(row["p_primary_category_id"])
        secondary_top, secondary_sub = self._resolve_category_id_to_pair(row["p_secondary_category_id"])

        return Podcast(
            id=as_str(row["p_id"]),
            created_at=row["p_created_at"],
            rss_url=row["rss_url"],
            title=row["p_title"],
            slug=row["p_slug"] or "",
            description=row["p_description"],
            image_url=row["p_image_url"],
            language=row["p_language"] if row["p_language"] else "en",
            primary_category=primary_top,
            primary_subcategory=primary_sub,
            secondary_category=secondary_top,
            secondary_subcategory=secondary_sub,
            # THES-142: New fields
            author=row["p_author"],
            explicit=_opt_bool(row["p_explicit"]),
            show_type=row["p_show_type"],
            website_url=row["p_website_url"],
            is_complete=bool(row["p_is_complete"]) if row["p_is_complete"] is not None else False,
            copyright=row["p_copyright"],
            last_processed=row["last_processed"],
            last_processed_at=row.get("last_processed_at"),
            episodes=[],  # Episodes not loaded
        )

    # ------------------------------------------------------------------
    # Episode → parent podcast lookups
    # ------------------------------------------------------------------

    def get_podcast_for_episode(self, episode_id: str) -> Optional[Podcast]:
        """
        Get the podcast that owns a specific episode.

        NOTE: the SQLite original projects only a subset of podcast columns
        here and then feeds ``_row_to_podcast`` (which reads them all) — a
        latent crash. The port selects the full canonical column list so
        the method behaves as documented.

        Args:
            episode_id: Episode UUID

        Returns:
            Podcast object if found, None otherwise
        """
        with self._get_connection() as conn:
            row = conn.execute(
                f"""
                SELECT {_PODCAST_COLS_P}
                FROM podcasts p
                INNER JOIN episodes e ON e.podcast_id = p.id
                WHERE e.id = %s
                """,
                (episode_id,),
            ).fetchone()
            if row:
                return self._row_to_podcast(row, conn)
            return None

    # ------------------------------------------------------------------
    # Import (paste-a-URL) helpers
    # ------------------------------------------------------------------

    def ensure_synthetic_audio_imports_parent(self) -> str:
        """
        Find-or-create the synthetic parent for bare-audio imports.

        The row is marked ``synthetic=true`` so refresh and discovery skip
        it. Returns the (deterministic) podcast id; callers store this as
        the ``podcast_id`` on imported episodes when no real parent can be
        deduced from the URL.
        """
        title = "Audio imports"
        now = datetime.now(timezone.utc)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug,
                                      description, language, synthetic, auto_added)
                VALUES (%s, %s, %s, %s, %s, %s,
                        'Synthetic parent for bare-audio imports.',
                        'en', true, false)
                ON CONFLICT (rss_url) DO NOTHING
                """,
                (
                    SYNTHETIC_AUDIO_IMPORTS_ID,
                    now,
                    now,
                    SYNTHETIC_AUDIO_IMPORTS_RSS,
                    title,
                    generate_slug(title),
                ),
            )
        return SYNTHETIC_AUDIO_IMPORTS_ID

    def upsert_auto_added_podcast(
        self,
        *,
        rss_url: str,
        title: str,
        description: str = "",
        image_url: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Find-or-create a real ``podcasts`` row for an import-deduced parent.

        Returns ``(id, title, slug)``. New rows are inserted with
        ``auto_added=true``; existing rows (whether previously auto-added or
        manually subscribed) are returned unchanged so a user who already
        follows the channel does not silently lose that signal.
        """
        podcast_id = str(_uuid.uuid4())
        now = datetime.now(timezone.utc)
        slug = generate_slug(title)
        with self._get_connection() as conn:
            inserted = conn.execute(
                """
                INSERT INTO podcasts
                    (id, created_at, updated_at, rss_url, title, slug,
                     description, image_url, language, synthetic, auto_added)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'en', false, true)
                ON CONFLICT (rss_url) DO NOTHING
                RETURNING id, title, slug
                """,
                (podcast_id, now, now, rss_url, title, slug, description, image_url),
            ).fetchone()
            if inserted is not None:
                return as_str(inserted["id"]), inserted["title"], inserted["slug"]
            existing = conn.execute("SELECT id, title, slug FROM podcasts WHERE rss_url = %s", (rss_url,)).fetchone()
            if existing is None:
                raise RuntimeError(
                    f"upsert_auto_added_podcast: row for rss_url={rss_url!r} " "neither inserted nor found"
                )
            return as_str(existing["id"]), existing["title"], existing["slug"]

    def get_real_parent_podcast_for_episode(self, episode_id: str) -> Optional[Tuple[str, str, str]]:
        """
        Return ``(id, title, slug)`` for the parent podcast of ``episode_id``
        IFF the parent is a real (non-synthetic) row — otherwise ``None``.

        Used by the import flow's dedup path to surface a follow target for
        already-imported episodes without re-hydrating the full Podcast
        model (which would also load every episode of the channel).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT p.id, p.title, p.slug
                  FROM episodes e
                  JOIN podcasts p ON p.id = e.podcast_id
                 WHERE e.id = %s AND p.synthetic = false
                """,
                (episode_id,),
            ).fetchone()
            if row is None:
                return None
            return as_str(row["id"]), row["title"], row["slug"]
