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

"""
PostgreSQL episode-side podcast repository mixin (spec #44).

Faithful port of the EPISODE-side methods of ``SqlitePodcastRepository`` to
Postgres, following the port conventions in ``utils/postgres_ext.py``:

- ``?`` placeholders → ``%s``.
- uuid columns (``episodes.id``, ``podcasts.id``, ``tasks.id``): str params
  bind directly; reads come back as ``uuid.UUID`` → wrapped with ``as_str``.
- Text-ISO timestamps → ``timestamptz``: pass tz-aware ``datetime`` objects,
  reads are tz-aware datetimes already (no isoformat/fromisoformat).
- SQLite 0/1 integer booleans (``explicit``, ``auto_process_excluded``,
  ``is_complete``) → native ``boolean``.
- ``INSERT OR IGNORE`` → ``ON CONFLICT ... DO NOTHING``; SQLite ``IS NOT ?``
  null-safe inequality → ``IS DISTINCT FROM %s``.
- ``episodes.duration`` is a TEXT column while ``Episode.duration`` is an
  ``int`` — SQLite's dynamic typing stored ints in the text column silently;
  Postgres is strict, so writes go through ``_duration_param`` (str) and reads
  rely on Pydantic's str→int coercion, preserving the model contract.
- SQLite's ASCII-case-insensitive ``LIKE`` for the user-facing episode title
  search → ``ILIKE``.

The mixin has NO ``__init__``: it expects the composing class to set
``self.dsn``. Schema DDL lives exclusively in ``postgres_schema.py``.

NOTE (cleanup): ``_normalize_artwork_url``, the minimal Podcast row mapping and
the category-map helpers are duplicated from the podcast-side port (developed
in parallel in ``postgres_podcast_repository_podcasts.py``); consolidate when
the two mixins are composed into the final repository class.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg
from structlog import get_logger

from ..models.podcast import Episode, EpisodeState, FailureType, Podcast, TranscriptLink
from ..utils.datetime_utils import now_utc
from ..utils.podcast_categories import normalize_category_name
from ..utils.postgres_ext import as_str, connect
from ..utils.slug import generate_slug

logger = get_logger(__name__)


def _normalize_artwork_url(url: Optional[str]) -> Optional[str]:
    """Upgrade ``http://`` artwork URLs to ``https://`` before storage.

    Same rationale as the SQLite implementation: the web UI's CSP is
    ``img-src 'self' data: https:`` so stored ``http://`` URLs are dropped by
    the browser; every artwork CDN serves the same path over TLS.
    """
    if url and url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _duration_param(duration: Optional[int]) -> Optional[str]:
    """``episodes.duration`` is TEXT; Postgres won't coerce int → text."""
    return None if duration is None else str(duration)


def _episode_from_row(row: Dict[str, Any], *, prefix: str = "") -> Episode:
    """
    Build an ``Episode`` from a Postgres dict row.

    ``prefix`` lets composed-JOIN queries alias episode columns (e.g. ``ep_*``)
    and reuse the same mapping. Unlike the SQLite mapper, timestamptz columns
    arrive as tz-aware datetimes and booleans as bools — no parsing.
    """

    def col(name: str):
        return row[f"{prefix}{name}"]

    def has(name: str) -> bool:
        return f"{prefix}{name}" in row

    failure_type = None
    if col("failure_type"):
        try:
            failure_type = FailureType(col("failure_type"))
        except ValueError:
            logger.warning(f"Unknown failure_type '{col('failure_type')}' for episode {as_str(col('id'))}")

    return Episode(
        id=as_str(col("id")),
        podcast_id=as_str(col("podcast_id")),
        created_at=col("created_at"),
        updated_at=col("updated_at"),
        external_id=col("external_id"),
        title=col("title"),
        slug=col("slug") or "",
        description=col("description"),
        description_html=col("description_html") or "",
        pub_date=col("pub_date"),
        audio_url=col("audio_url"),
        duration=col("duration"),  # TEXT column; Pydantic coerces "3600" → 3600
        image_url=col("image_url"),
        explicit=col("explicit"),
        episode_type=col("episode_type"),
        episode_number=col("episode_number"),
        season_number=col("season_number"),
        website_url=col("website_url"),
        audio_file_size=col("audio_file_size"),
        audio_mime_type=col("audio_mime_type"),
        audio_path=col("audio_path"),
        downsampled_audio_path=col("downsampled_audio_path"),
        raw_transcript_path=col("raw_transcript_path"),
        clean_transcript_path=col("clean_transcript_path"),
        clean_transcript_json_path=(col("clean_transcript_json_path") if has("clean_transcript_json_path") else None),
        playback_time_offset_seconds=(
            col("playback_time_offset_seconds")
            if has("playback_time_offset_seconds") and col("playback_time_offset_seconds") is not None
            else 0.0
        ),
        summary_path=col("summary_path"),
        published_at=(col("published_at") if has("published_at") else None),
        failed_at_stage=col("failed_at_stage"),
        failure_reason=col("failure_reason"),
        failure_type=failure_type,
        failed_at=col("failed_at"),
    )


# Aliased podcast columns for (Podcast, Episode) tuple queries — identical
# projection to the SQLite implementation's inline SELECT lists.
_PODCAST_TUPLE_COLS = """p.id AS p_id, p.created_at AS p_created_at, p.rss_url, p.title AS p_title,
       p.slug AS p_slug, p.description AS p_description, p.image_url AS p_image_url,
       p.language AS p_language,
       p.primary_category_id AS p_primary_category_id,
       p.secondary_category_id AS p_secondary_category_id,
       p.author AS p_author, p.explicit AS p_explicit, p.show_type AS p_show_type,
       p.website_url AS p_website_url, p.is_complete AS p_is_complete, p.copyright AS p_copyright,
       p.last_processed, p.last_processed_at, p.updated_at AS p_updated_at, e.*"""


class EpisodesMixin:
    """Episode-side methods of the Postgres podcast repository.

    Composed (with the podcast-side mixin) into the concrete
    ``PostgresPodcastRepository``. Requires ``self.dsn`` to be set by the
    composing class; uses connection-per-operation via ``postgres_ext.connect``.
    """

    dsn: str

    # ------------------------------------------------------------------
    # Category lookups (duplicated minimal mapping — see module docstring)
    # ------------------------------------------------------------------

    def _category_maps(
        self, conn: psycopg.Connection
    ) -> Tuple[Dict[Tuple[str, Optional[str]], int], Dict[int, Tuple[str, Optional[str]]]]:
        """Load ``(pair→id, id→pair)`` category maps from the categories table.

        The SQLite repository builds these caches once at ``__init__`` after
        seeding; here they're loaded per operation on the already-open
        connection (the table is ~200 small rows). Keys of ``pair→id`` are
        normalized via ``normalize_category_name``; top-level rows are stored
        under ``(top_norm, None)``.
        """
        rows = conn.execute("SELECT id, name, parent_id FROM categories").fetchall()
        top_id_to_name = {r["id"]: r["name"] for r in rows if r["parent_id"] is None}
        pair_to_id: Dict[Tuple[str, Optional[str]], int] = {}
        id_to_pair: Dict[int, Tuple[str, Optional[str]]] = {}
        for r in rows:
            if r["parent_id"] is None:
                id_to_pair[r["id"]] = (r["name"], None)
                pair_to_id[(normalize_category_name(r["name"]), None)] = r["id"]
        for r in rows:
            if r["parent_id"] is not None:
                top_name = top_id_to_name.get(r["parent_id"])
                if top_name is None:
                    continue  # orphan subcategory — defensive, FK should prevent
                id_to_pair[r["id"]] = (top_name, r["name"])
                pair_to_id[(normalize_category_name(top_name), normalize_category_name(r["name"]))] = r["id"]
        return pair_to_id, id_to_pair

    @staticmethod
    def _resolve_category_strings(
        pair_to_id: Dict[Tuple[str, Optional[str]], int], top: Optional[str], sub: Optional[str]
    ) -> Optional[int]:
        """Most-specific category FK id for the given strings (best-effort,
        same fallbacks as the SQLite ``_resolve_category_strings_to_id``)."""
        if not top:
            return None
        top_norm = normalize_category_name(top)
        top_id = pair_to_id.get((top_norm, None))
        if top_id is None:
            return None
        if not sub:
            return top_id
        sub_id = pair_to_id.get((top_norm, normalize_category_name(sub)))
        return sub_id if sub_id is not None else top_id

    def _podcast_from_row_minimal(
        self, row: Dict[str, Any], id_to_pair: Dict[int, Tuple[str, Optional[str]]]
    ) -> Podcast:
        """Convert an aliased ``p_*`` row to a Podcast model without episodes.

        NOTE (cleanup): duplicates the podcast-side mixin's minimal mapping;
        consolidate when the mixins are composed.
        """
        # None / unknown FK ids resolve to (None, None) — same as the SQLite
        # ``_resolve_category_id_to_pair``.
        primary_top, primary_sub = id_to_pair.get(row["p_primary_category_id"], (None, None))
        secondary_top, secondary_sub = id_to_pair.get(row["p_secondary_category_id"], (None, None))

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
            author=row["p_author"],
            explicit=row["p_explicit"],
            show_type=row["p_show_type"],
            website_url=row["p_website_url"],
            is_complete=bool(row["p_is_complete"]) if row["p_is_complete"] is not None else False,
            copyright=row["p_copyright"],
            last_processed=row["last_processed"],
            last_processed_at=row.get("last_processed_at"),
            episodes=[],  # Episodes not loaded
        )

    # ------------------------------------------------------------------
    # Status / health counters
    # ------------------------------------------------------------------

    def count_episodes_skipped_legacy(self) -> int:
        """Spec #28 Phase 3.4 — episodes the entity branch declined to process.

        The SQLite version guards against pre-migration databases missing the
        ``entity_extraction_status`` column; the Postgres schema is canonical
        (``postgres_schema.py``) but the same defensiveness is preserved for
        partially-provisioned databases.
        """
        try:
            with connect(self.dsn) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM episodes " "WHERE entity_extraction_status = 'skipped_legacy'"
                ).fetchone()
        except (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable):
            return 0
        if row is None:
            return 0
        return int(row["n"] or 0)

    # ------------------------------------------------------------------
    # Episode writes
    # ------------------------------------------------------------------

    def save_episode(self, episode: Episode) -> Episode:
        """
        Save or update a single episode.

        Idempotent: Only updates updated_at if data actually changed.
        Requires: episode.podcast_id must be set.
        """
        if not episode.podcast_id:
            raise ValueError("episode.podcast_id must be set before saving")

        with connect(self.dsn) as conn:
            return self._save_episode_idempotent(conn, episode)

    def save_episodes(self, episodes: List[Episode]) -> List[Episode]:
        """
        Save or update multiple episodes in a single transaction.

        Idempotent: Only updates updated_at for episodes with actual changes.
        Requires: Each episode.podcast_id must be set.
        """
        if not episodes:
            return []

        # Validate all episodes have podcast_id
        for ep in episodes:
            if not ep.podcast_id:
                raise ValueError(f"episode.podcast_id must be set for episode: {ep.title}")

        with connect(self.dsn) as conn:
            return [self._save_episode_idempotent(conn, ep) for ep in episodes]

    def save_refresh_batch(
        self,
        changed_podcasts: List[Podcast],
        new_episodes: List[Episode],
        episode_image_updates: Optional[List[Tuple[str, str, Optional[str]]]] = None,
    ) -> None:
        """
        Commit one refresh's worth of state in a single transaction (spec #19).

        Blind podcast-meta UPDATE keyed by id, ``ON CONFLICT DO NOTHING``
        episode inserts (the concurrent-refresh backstop that was
        ``INSERT OR IGNORE`` on SQLite), and guarded artwork re-syncs
        (``IS DISTINCT FROM`` replaces SQLite's ``IS NOT ?``) so only drifted
        rows write. See the SQLite docstring for the full rationale.
        """
        if not changed_podcasts and not new_episodes and not episode_image_updates:
            return

        for ep in new_episodes:
            if not ep.podcast_id:
                raise ValueError(f"episode.podcast_id must be set for episode: {ep.title}")

        now = datetime.now(timezone.utc)
        with connect(self.dsn) as conn:
            cur = conn.cursor()
            # Blind UPDATE keyed by id — the refresh loop already chose
            # these rows to write, so we skip the read-then-diff of
            # ``save_podcast``.
            if changed_podcasts:
                pair_to_id, _ = self._category_maps(conn)
                podcast_params = [
                    (
                        p.title,
                        p.slug,
                        p.description,
                        _normalize_artwork_url(p.image_url),
                        p.language,
                        self._resolve_category_strings(pair_to_id, p.primary_category, p.primary_subcategory),
                        self._resolve_category_strings(pair_to_id, p.secondary_category, p.secondary_subcategory),
                        p.author,
                        p.explicit,
                        p.show_type,
                        p.website_url,
                        p.is_complete,
                        p.copyright,
                        p.last_processed,
                        p.etag,
                        p.last_modified,
                        now,
                        p.id,
                    )
                    for p in changed_podcasts
                ]
                cur.executemany(
                    """
                    UPDATE podcasts
                    SET title = %s, slug = %s, description = %s, image_url = %s, language = %s,
                        primary_category_id = %s, secondary_category_id = %s,
                        author = %s, explicit = %s, show_type = %s, website_url = %s, is_complete = %s, copyright = %s,
                        last_processed = %s, etag = %s, last_modified = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    podcast_params,
                )

            # Refresh only discovers brand-new episodes, so ON CONFLICT DO
            # NOTHING defends against the rare concurrent-refresh race on
            # ``(podcast_id, external_id)``.
            episode_params = [
                (
                    ep.id,
                    ep.podcast_id,
                    ep.created_at,
                    now,
                    ep.external_id,
                    ep.title,
                    ep.slug,
                    ep.description,
                    ep.description_html,
                    ep.pub_date,
                    str(ep.audio_url),
                    _duration_param(ep.duration),
                    _normalize_artwork_url(ep.image_url),
                    ep.explicit,
                    ep.episode_type,
                    ep.episode_number,
                    ep.season_number,
                    ep.website_url,
                    ep.audio_file_size,
                    ep.audio_mime_type,
                    ep.audio_path,
                    ep.downsampled_audio_path,
                    ep.raw_transcript_path,
                    ep.clean_transcript_path,
                    ep.clean_transcript_json_path,
                    ep.summary_path,
                    ep.playback_time_offset_seconds,
                )
                for ep in new_episodes
            ]
            if episode_params:
                cur.executemany(
                    """
                    INSERT INTO episodes (
                        id, podcast_id, created_at, updated_at, external_id, title, slug, description,
                        description_html, pub_date, audio_url, duration, image_url,
                        explicit, episode_type, episode_number, season_number, website_url,
                        audio_file_size, audio_mime_type,
                        audio_path, downsampled_audio_path, raw_transcript_path, clean_transcript_path,
                        clean_transcript_json_path, summary_path, playback_time_offset_seconds
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (podcast_id, external_id) DO NOTHING
                    """,
                    episode_params,
                )

            # Re-sync drifted artwork for existing episodes (stale signed-URL
            # repair). Keyed by (podcast_id, external_id) so no episode
            # hydration is needed; the ``IS DISTINCT FROM`` guard keeps it a
            # no-op — and leaves ``updated_at`` untouched — unless the URL
            # actually changed. New episodes inserted just above already carry
            # the current URL, so their update here is a guarded no-op.
            if episode_image_updates:
                image_params = [
                    (
                        _normalize_artwork_url(image_url),
                        now,
                        podcast_id,
                        external_id,
                        _normalize_artwork_url(image_url),
                    )
                    for podcast_id, external_id, image_url in episode_image_updates
                ]
                cur.executemany(
                    """
                    UPDATE episodes
                    SET image_url = %s, updated_at = %s
                    WHERE podcast_id = %s AND external_id = %s AND image_url IS DISTINCT FROM %s
                    """,
                    image_params,
                )

    def update_episode_image_urls(self, updates: List[Tuple[str, Optional[str]]]) -> int:
        """Update ``image_url`` for existing episodes in one transaction.

        Image-repair routine (see SQLite docstring): re-syncs stored artwork
        URLs from the live feed. The ``IS DISTINCT FROM`` guard skips no-op
        writes so ``updated_at`` only moves when the value actually changed.

        Returns:
            Number of rows actually changed.
        """
        if not updates:
            return 0
        now = datetime.now(timezone.utc)
        changed = 0
        with connect(self.dsn) as conn:
            for episode_id, image_url in updates:
                normalized = _normalize_artwork_url(image_url)
                cursor = conn.execute(
                    """
                    UPDATE episodes
                    SET image_url = %s, updated_at = %s
                    WHERE id = %s AND image_url IS DISTINCT FROM %s
                    """,
                    (normalized, now, episode_id, normalized),
                )
                changed += cursor.rowcount
        return changed

    # ------------------------------------------------------------------
    # Spec #48 — orphan recovery / auto-process gating (tasks is read-only)
    # ------------------------------------------------------------------

    def get_discovered_unqueued_episodes(
        self, podcast_id: str, within_days: int = 2, limit: int = 25
    ) -> List[Tuple[str, Optional[str]]]:
        """Spec #48 P1 — RECENT episodes persisted but never enqueued (orphans).

        Same idempotent crash-recovery query as the SQLite version (see its
        docstring for the full rationale): DISCOVERED state (no artifact
        paths, not failed, not auto-process-excluded) with no task row at all,
        scoped to ``within_days`` and bounded by ``limit``. Returns
        ``(episode_id, audio_url)`` pairs.
        """
        cutoff = now_utc() - timedelta(days=within_days)
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.audio_url
                FROM episodes e
                WHERE e.podcast_id = %s
                  AND e.created_at >= %s
                  AND e.failed_at_stage IS NULL
                  AND e.auto_process_excluded = false
                  AND e.audio_path IS NULL
                  AND e.downsampled_audio_path IS NULL
                  AND e.raw_transcript_path IS NULL
                  AND e.clean_transcript_path IS NULL
                  AND e.summary_path IS NULL
                  AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.episode_id = e.id)
                ORDER BY e.pub_date DESC
                LIMIT %s
                """,
                (podcast_id, cutoff, limit),
            ).fetchall()
            return [(as_str(row["id"]), row["audio_url"]) for row in rows]

    def get_unqueued_unprocessed_episodes(self, episode_ids: List[str]) -> List[Tuple[str, Optional[str]]]:
        """Filter ``episode_ids`` to those that still need the full pipeline.

        Same predicate as ``get_discovered_unqueued_episodes`` but scoped to a
        caller-supplied set and NOT time-windowed (a follow can legitimately
        seed an old-but-never-processed episode). Returns
        ``(episode_id, audio_url)`` pairs.
        """
        if not episode_ids:
            return []
        placeholders = ",".join("%s" for _ in episode_ids)
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT e.id, e.audio_url
                FROM episodes e
                WHERE e.id IN ({placeholders})
                  AND e.failed_at_stage IS NULL
                  AND e.audio_path IS NULL
                  AND e.downsampled_audio_path IS NULL
                  AND e.raw_transcript_path IS NULL
                  AND e.clean_transcript_path IS NULL
                  AND e.summary_path IS NULL
                  AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.episode_id = e.id)
                """,
                tuple(episode_ids),
            ).fetchall()
            return [(as_str(row["id"]), row["audio_url"]) for row in rows]

    def get_recent_unqueued_unprocessed_episodes(self, podcast_id: str, limit: int) -> List[Tuple[str, Optional[str]]]:
        """The podcast's ``limit`` most-recent un-started episodes, by air date.

        Drives the subscribe-time transcription backlog (see the SQLite
        docstring): ordered by ``COALESCE(pub_date, published_at) DESC``,
        untouched orphans only, not time-windowed.
        """
        if limit <= 0:
            return []
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.audio_url
                FROM episodes e
                WHERE e.podcast_id = %s
                  AND e.failed_at_stage IS NULL
                  AND e.auto_process_excluded = false
                  AND e.audio_path IS NULL
                  AND e.downsampled_audio_path IS NULL
                  AND e.raw_transcript_path IS NULL
                  AND e.clean_transcript_path IS NULL
                  AND e.summary_path IS NULL
                  AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.episode_id = e.id)
                ORDER BY COALESCE(e.pub_date, e.published_at) DESC
                LIMIT %s
                """,
                (podcast_id, limit),
            ).fetchall()
            return [(as_str(row["id"]), row["audio_url"]) for row in rows]

    def has_processed_episodes(self, podcast_id: str) -> bool:
        """True once the podcast has at least one episode past discovery.

        "Processed" means any pipeline progress — an artifact path or a publish
        timestamp. Distinguishes a brand-new podcast still in its initial
        backfill from an established one.
        """
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM episodes
                WHERE podcast_id = %s
                  AND (published_at IS NOT NULL
                       OR raw_transcript_path IS NOT NULL
                       OR clean_transcript_path IS NOT NULL
                       OR summary_path IS NOT NULL)
                LIMIT 1
                """,
                (podcast_id,),
            ).fetchone()
            return row is not None

    def count_episodes_with_tasks(self, podcast_id: str) -> int:
        """Number of the podcast's episodes that have ever had a queue task.

        Used to size the initial-backfill cap (see SQLite docstring).
        """
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM episodes e
                WHERE e.podcast_id = %s
                  AND EXISTS (SELECT 1 FROM tasks t WHERE t.episode_id = e.id)
                """,
                (podcast_id,),
            ).fetchone()
            return row["n"] if row else 0

    def mark_episodes_auto_process_excluded(self, episode_ids: List[str]) -> int:
        """Flag ``episode_ids`` so the refresh-feed sweep never auto-enqueues them.

        Applied to a brand-new podcast's back catalog beyond the most-recent N.
        Idempotent. Returns the number of rows updated.
        """
        if not episode_ids:
            return 0
        placeholders = ",".join("%s" for _ in episode_ids)
        with connect(self.dsn) as conn:
            cur = conn.execute(
                f"UPDATE episodes SET auto_process_excluded = true WHERE id IN ({placeholders})",
                tuple(episode_ids),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Internal save paths
    # ------------------------------------------------------------------

    def _save_episode_idempotent(self, conn: psycopg.Connection, episode: Episode) -> Episode:
        """
        Internal: Save episode with idempotent updated_at handling.

        Only updates updated_at if data actually changed.
        """
        now = datetime.now(timezone.utc)

        # Check if episode exists (by podcast_id + external_id).
        # IMPORTANT: every column the UPDATE below writes must also be SELECTed
        # and compared in the `changed` check, otherwise updates to those
        # fields are silently dropped when no other field happens to differ.
        existing = conn.execute(
            """
            SELECT id, title, slug, description, description_html, pub_date, audio_url, duration, image_url,
                   explicit, episode_type, episode_number, season_number, website_url,
                   audio_file_size, audio_mime_type,
                   audio_path, downsampled_audio_path, raw_transcript_path,
                   clean_transcript_path, clean_transcript_json_path, summary_path,
                   playback_time_offset_seconds
            FROM episodes
            WHERE podcast_id = %s AND external_id = %s
            """,
            (episode.podcast_id, episode.external_id),
        ).fetchone()

        if existing:
            # Compare fields to see if anything changed. timestamptz and
            # boolean columns compare natively; duration compares as the TEXT
            # the column stores.
            duration_str = _duration_param(episode.duration)
            normalized_image_url = _normalize_artwork_url(episode.image_url)

            changed = (
                existing["title"] != episode.title
                or existing["slug"] != episode.slug
                or existing["description"] != episode.description
                or existing["description_html"] != episode.description_html
                or existing["pub_date"] != episode.pub_date
                or existing["audio_url"] != str(episode.audio_url)
                or existing["duration"] != duration_str
                or existing["image_url"] != normalized_image_url
                or existing["explicit"] != episode.explicit
                or existing["episode_type"] != episode.episode_type
                or existing["episode_number"] != episode.episode_number
                or existing["season_number"] != episode.season_number
                or existing["website_url"] != episode.website_url
                or existing["audio_file_size"] != episode.audio_file_size
                or existing["audio_mime_type"] != episode.audio_mime_type
                or existing["audio_path"] != episode.audio_path
                or existing["downsampled_audio_path"] != episode.downsampled_audio_path
                or existing["raw_transcript_path"] != episode.raw_transcript_path
                or existing["clean_transcript_path"] != episode.clean_transcript_path
                or existing["clean_transcript_json_path"] != episode.clean_transcript_json_path
                or existing["summary_path"] != episode.summary_path
                or existing["playback_time_offset_seconds"] != episode.playback_time_offset_seconds
            )

            if changed:
                # Update with new updated_at
                conn.execute(
                    """
                    UPDATE episodes
                    SET title = %s, slug = %s, description = %s, description_html = %s, pub_date = %s, audio_url = %s,
                        duration = %s, image_url = %s,
                        explicit = %s, episode_type = %s, episode_number = %s, season_number = %s, website_url = %s,
                        audio_file_size = %s, audio_mime_type = %s,
                        audio_path = %s, downsampled_audio_path = %s,
                        raw_transcript_path = %s, clean_transcript_path = %s,
                        clean_transcript_json_path = %s, summary_path = %s,
                        playback_time_offset_seconds = %s,
                        updated_at = %s
                    WHERE podcast_id = %s AND external_id = %s
                    """,
                    (
                        episode.title,
                        episode.slug,
                        episode.description,
                        episode.description_html,
                        episode.pub_date,
                        str(episode.audio_url),
                        duration_str,
                        normalized_image_url,
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
                        # spec #18: segmented-cleanup sidecar + playback offset
                        episode.clean_transcript_json_path,
                        episode.summary_path,
                        episode.playback_time_offset_seconds,
                        now,
                        episode.podcast_id,
                        episode.external_id,
                    ),
                )
                logger.debug(f"Updated episode: {episode.title}")
            else:
                logger.debug(f"Episode unchanged: {episode.title}")
        else:
            # Insert new episode
            conn.execute(
                """
                INSERT INTO episodes (
                    id, podcast_id, created_at, updated_at, external_id, title, slug, description,
                    description_html, pub_date, audio_url, duration, image_url,
                    explicit, episode_type, episode_number, season_number, website_url,
                    audio_file_size, audio_mime_type,
                    audio_path, downsampled_audio_path, raw_transcript_path, clean_transcript_path,
                    clean_transcript_json_path, summary_path, playback_time_offset_seconds
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    episode.id,
                    episode.podcast_id,
                    episode.created_at,
                    now,
                    episode.external_id,
                    episode.title,
                    episode.slug,
                    episode.description,
                    episode.description_html,
                    episode.pub_date,
                    str(episode.audio_url),
                    _duration_param(episode.duration),
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
                    # spec #18: segmented-cleanup sidecar + playback offset
                    episode.clean_transcript_json_path,
                    episode.summary_path,
                    episode.playback_time_offset_seconds,
                ),
            )
            logger.debug(f"Inserted new episode: {episode.title}")

        return episode

    def update_episode(self, podcast_url: str, episode_external_id: str, updates: dict) -> bool:
        """
        Update specific episode fields.

        Side effects: updated_at set explicitly here (no trigger).
        """
        # Build dynamic UPDATE query (safe: we validate field names)
        valid_fields = {
            "audio_path",
            "downsampled_audio_path",
            "raw_transcript_path",
            "clean_transcript_path",
            # spec #18: segmented-cleanup sidecar path
            "clean_transcript_json_path",
            "playback_time_offset_seconds",
            "summary_path",
            "title",
            "slug",
            "description",
            "description_html",
            "duration",
            "image_url",
            # THES-142: New fields
            "explicit",
            "episode_type",
            "episode_number",
            "season_number",
            "website_url",
            "audio_file_size",
            "audio_mime_type",
            # Failure tracking fields
            "failed_at_stage",
            "failure_reason",
            "failure_type",
            "failed_at",
        }

        update_fields = {k: v for k, v in updates.items() if k in valid_fields}
        if not update_fields:
            return False

        # SQLite's dynamic typing tolerated int durations in the TEXT column
        # and str-subclass enums; Postgres is strict, so coerce at the seam.
        if "duration" in update_fields:
            update_fields["duration"] = _duration_param(update_fields["duration"])
        if isinstance(update_fields.get("failure_type"), FailureType):
            update_fields["failure_type"] = update_fields["failure_type"].value

        set_clause = ", ".join(f"{field} = %s" for field in update_fields.keys())
        values = list(update_fields.values())

        now = datetime.now(timezone.utc)

        with connect(self.dsn) as conn:
            cursor = conn.execute(
                f"""
                UPDATE episodes
                SET {set_clause}, updated_at = %s
                WHERE podcast_id = (SELECT id FROM podcasts WHERE rss_url = %s)
                  AND external_id = %s
                """,
                values + [now, podcast_url, episode_external_id],
            )

            updated = cursor.rowcount > 0
            if updated:
                logger.debug(f"Updated episode {episode_external_id}: {list(update_fields.keys())}")
            return updated

    # ------------------------------------------------------------------
    # Publish / failure state
    # ------------------------------------------------------------------

    def mark_episode_published(self, episode_id: str) -> bool:
        """Set ``published_at`` if not already set; return whether it transitioned."""
        now = datetime.now(timezone.utc)
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE episodes
                   SET published_at = %s, updated_at = %s
                 WHERE id = %s AND published_at IS NULL
                """,
                (now, now, episode_id),
            )
            return cursor.rowcount == 1

    def mark_episode_failed(
        self,
        episode_id: str,
        failed_at_stage: str,
        failure_reason: str,
        failure_type: str,
    ) -> bool:
        """
        Mark an episode as failed at a specific stage.

        Called when a task exhausts its retries (transient) or hits a fatal
        error. Returns True if the episode was updated, False if not found.
        """
        now = datetime.now(timezone.utc)

        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE episodes
                SET failed_at_stage = %s,
                    failure_reason = %s,
                    failure_type = %s,
                    failed_at = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (failed_at_stage, failure_reason, failure_type, now, now, episode_id),
            )

            updated = cursor.rowcount > 0
            if updated:
                logger.info(f"Marked episode {episode_id} as failed at stage '{failed_at_stage}' ({failure_type})")
            else:
                logger.warning(f"Failed to mark episode {episode_id} as failed: not found")
            return updated

    def clear_episode_failure(self, episode_id: str) -> bool:
        """
        Clear failure state from an episode, allowing retry.

        Called when manually retrying a failed episode from the DLQ.
        Returns True if the episode was updated, False if not found.
        """
        now = datetime.now(timezone.utc)

        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE episodes
                SET failed_at_stage = NULL,
                    failure_reason = NULL,
                    failure_type = NULL,
                    failed_at = NULL,
                    updated_at = %s
                WHERE id = %s
                """,
                (now, episode_id),
            )

            updated = cursor.rowcount > 0
            if updated:
                logger.info(f"Cleared failure state for episode {episode_id}")
            else:
                logger.warning(f"Failed to clear failure for episode {episode_id}: not found")
            return updated

    def clear_episode_failure_for_stages(self, episode_id: str, stages: Sequence[str]) -> bool:
        """Clear an episode's failure banner only if it was recorded at one of ``stages``.

        Called on successful stage completion (see the SQLite docstring):
        scoping by stage means a success at an earlier stage does not wipe a
        failure recorded at a later, not-yet-rerun stage.
        """
        if not stages:
            return False

        now = datetime.now(timezone.utc)
        placeholders = ",".join(["%s"] * len(stages))

        with connect(self.dsn) as conn:
            cursor = conn.execute(
                f"""
                UPDATE episodes
                SET failed_at_stage = NULL,
                    failure_reason = NULL,
                    failure_type = NULL,
                    failed_at = NULL,
                    updated_at = %s
                WHERE id = %s
                  AND failed_at_stage IN ({placeholders})
                """,
                (now, episode_id, *stages),
            )

            updated = cursor.rowcount > 0
            if updated:
                logger.info(
                    "Cleared stale failure for episode %s on successful stage completion",
                    episode_id,
                )
            return updated

    def update_entity_extraction_status(self, episode_id: str, status: str) -> bool:
        """Set ``episodes.entity_extraction_status`` for one episode.

        Spec #28 §6 ("Failure isolation rule"): the entity branch progresses
        independently of the user-facing pipeline — this lives in its own
        status column and never touches ``failed_at_stage``. Allowed values:
        ``pending`` | ``complete`` | ``failed`` | ``skipped_legacy``
        (validation is the caller's responsibility).
        """
        now = datetime.now(timezone.utc)
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE episodes
                SET entity_extraction_status = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (status, now, episode_id),
            )
            updated = cursor.rowcount > 0
            if updated:
                logger.info(
                    "entity_extraction_status updated",
                    episode_id=episode_id,
                    status=status,
                )
            else:
                logger.warning(
                    "entity_extraction_status update missed: episode not found",
                    episode_id=episode_id,
                )
            return updated

    def get_failed_episodes(self, limit: int = 100) -> List[Tuple[Podcast, Episode]]:
        """
        Get episodes in failed state.

        Returns (Podcast, Episode) tuples ordered by most recent failure first.
        """
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_PODCAST_TUPLE_COLS}
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.failed_at_stage IS NOT NULL
                ORDER BY e.failed_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

            _, id_to_pair = self._category_maps(conn)
            return [(self._podcast_from_row_minimal(row, id_to_pair), self._row_to_episode(row)) for row in rows]

    # ============================================================================
    # EpisodeRepository Interface Implementation
    # ============================================================================

    def get_episodes_by_podcast(self, podcast_url: str) -> List[Episode]:
        """Get all episodes for a podcast."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE p.rss_url = %s
                ORDER BY e.pub_date DESC
                """,
                (podcast_url,),
            ).fetchall()

            return [self._row_to_episode(row) for row in rows]

    def get_episode(self, episode_id: str) -> Optional[Tuple[Podcast, Episode]]:
        """Get episode by internal UUID (primary key)."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                SELECT {_PODCAST_TUPLE_COLS}
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.id = %s
                """,
                (episode_id,),
            ).fetchone()

            if not row:
                return None

            _, id_to_pair = self._category_maps(conn)
            return (self._podcast_from_row_minimal(row, id_to_pair), self._row_to_episode(row))

    def get_episode_by_external_id(self, podcast_url: str, episode_external_id: str) -> Optional[Episode]:
        """Get specific episode by external ID (from RSS feed)."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT e.*
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE p.rss_url = %s AND e.external_id = %s
                """,
                (podcast_url, episode_external_id),
            ).fetchone()

            return self._row_to_episode(row) if row else None

    def get_episode_by_slug(self, podcast_slug: str, episode_slug: str) -> Optional[Tuple[Podcast, Episode]]:
        """Get episode by podcast slug and episode slug."""
        if not podcast_slug or not episode_slug:
            return None

        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                SELECT {_PODCAST_TUPLE_COLS}
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE p.slug = %s AND e.slug = %s
                """,
                (podcast_slug, episode_slug),
            ).fetchone()

            if not row:
                return None

            _, id_to_pair = self._category_maps(conn)
            return (self._podcast_from_row_minimal(row, id_to_pair), self._row_to_episode(row))

    def get_unprocessed_episodes(self, state: str) -> List[Tuple[Podcast, Episode]]:
        """
        Get episodes in specific processing state.

        The WHERE clauses match the partial state indexes in
        ``postgres_schema.py`` (same shape as the SQLite partial indexes).
        """
        # Map state to SQL condition (matches partial index WHERE clauses)
        state_conditions = {
            EpisodeState.DISCOVERED.value: "e.audio_path IS NULL",
            EpisodeState.DOWNLOADED.value: "e.audio_path IS NOT NULL AND e.downsampled_audio_path IS NULL",
            EpisodeState.DOWNSAMPLED.value: "e.downsampled_audio_path IS NOT NULL AND e.raw_transcript_path IS NULL",
            EpisodeState.TRANSCRIBED.value: "e.raw_transcript_path IS NOT NULL AND e.clean_transcript_path IS NULL",
            EpisodeState.CLEANED.value: "e.clean_transcript_path IS NOT NULL AND e.summary_path IS NULL",
        }

        condition = state_conditions.get(state)
        if not condition:
            logger.warning(f"Unknown processing state: {state}")
            return []

        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_PODCAST_TUPLE_COLS}
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE {condition}
                ORDER BY e.pub_date DESC
                """
            ).fetchall()

            _, id_to_pair = self._category_maps(conn)
            return [(self._podcast_from_row_minimal(row, id_to_pair), self._row_to_episode(row)) for row in rows]

    def get_all_episodes(
        self,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        podcast_id: Optional[str] = None,
        state: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        updated_from: Optional[datetime] = None,
        sort_by: str = "pub_date",
        sort_order: str = "desc",
    ) -> Tuple[List[Tuple[Podcast, Episode]], int]:
        """
        Get episodes across all podcasts with filtering and pagination.

        ``search`` uses ILIKE (user-facing fuzzy search — SQLite LIKE is
        ASCII-case-insensitive, ILIKE is the Postgres equivalent).

        Returns (episodes_with_podcasts, total_count).
        """
        # Build WHERE conditions
        conditions = []
        params: List[Any] = []

        if search:
            conditions.append("e.title ILIKE %s")
            params.append(f"%{search}%")

        if podcast_id:
            conditions.append("e.podcast_id = %s")
            params.append(podcast_id)

        if state:
            # Map state to SQL condition matching the Episode.state computed property logic.
            # The model checks states from most-progressed first: FAILED > SUMMARIZED > CLEANED >
            # TRANSCRIBED > DOWNSAMPLED > DOWNLOADED > DISCOVERED.
            # Each SQL condition must exclude episodes that would be classified as a more-progressed state.
            state_conditions = {
                EpisodeState.FAILED.value: "e.failed_at_stage IS NOT NULL",
                EpisodeState.SUMMARIZED.value: ("e.summary_path IS NOT NULL " "AND e.failed_at_stage IS NULL"),
                EpisodeState.CLEANED.value: (
                    "e.clean_transcript_path IS NOT NULL " "AND e.summary_path IS NULL " "AND e.failed_at_stage IS NULL"
                ),
                EpisodeState.TRANSCRIBED.value: (
                    "e.raw_transcript_path IS NOT NULL "
                    "AND e.clean_transcript_path IS NULL "
                    "AND e.summary_path IS NULL "
                    "AND e.failed_at_stage IS NULL"
                ),
                EpisodeState.DOWNSAMPLED.value: (
                    "e.downsampled_audio_path IS NOT NULL "
                    "AND e.raw_transcript_path IS NULL "
                    "AND e.clean_transcript_path IS NULL "
                    "AND e.summary_path IS NULL "
                    "AND e.failed_at_stage IS NULL"
                ),
                EpisodeState.DOWNLOADED.value: (
                    "e.audio_path IS NOT NULL "
                    "AND e.downsampled_audio_path IS NULL "
                    "AND e.raw_transcript_path IS NULL "
                    "AND e.clean_transcript_path IS NULL "
                    "AND e.summary_path IS NULL "
                    "AND e.failed_at_stage IS NULL"
                ),
                EpisodeState.DISCOVERED.value: (
                    "e.audio_path IS NULL "
                    "AND e.downsampled_audio_path IS NULL "
                    "AND e.raw_transcript_path IS NULL "
                    "AND e.clean_transcript_path IS NULL "
                    "AND e.summary_path IS NULL "
                    "AND e.failed_at_stage IS NULL"
                ),
            }
            condition = state_conditions.get(state)
            if condition:
                conditions.append(f"({condition})")

        if date_from:
            conditions.append("e.pub_date >= %s")
            params.append(date_from)

        if date_to:
            conditions.append("e.pub_date <= %s")
            params.append(date_to)

        if updated_from:
            conditions.append("e.updated_at >= %s")
            params.append(updated_from)

        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Validate and build ORDER BY clause
        valid_sort_fields = {"pub_date": "e.pub_date", "title": "e.title", "updated_at": "e.updated_at"}
        sort_field = valid_sort_fields.get(sort_by, "e.pub_date")
        order_direction = "ASC" if sort_order.lower() == "asc" else "DESC"

        with connect(self.dsn) as conn:
            # Get total count
            count_query = f"""
                SELECT COUNT(*) AS total
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE {where_clause}
            """
            total = conn.execute(count_query, params).fetchone()["total"]

            # Get paginated results
            query = f"""
                SELECT {_PODCAST_TUPLE_COLS}
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE {where_clause}
                ORDER BY {sort_field} {order_direction}
                LIMIT %s OFFSET %s
            """
            rows = conn.execute(query, params + [limit, offset]).fetchall()

            _, id_to_pair = self._category_maps(conn)
            results = [
                (self._podcast_from_row_minimal(row, id_to_pair), self._row_to_episode(row)) for row in rows
            ]

            return results, total

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _row_to_episode(self, row: Dict[str, Any]) -> Episode:
        """Convert database row to Episode model."""
        return _episode_from_row(row)

    def _save_episode(self, conn: psycopg.Connection, podcast_id: str, episode: Episode, now: datetime):
        """Insert episode into database (destructive-save path)."""
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
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                _duration_param(episode.duration),
                _normalize_artwork_url(episode.image_url),
                # THES-142: New fields
                episode.explicit,
                episode.episode_type,
                episode.episode_number,
                episode.season_number,
                episode.website_url,
                episode.audio_file_size,
                episode.audio_mime_type,
                # File paths
                episode.audio_path,
                episode.downsampled_audio_path,
                episode.raw_transcript_path,
                episode.clean_transcript_path,
                # spec #18: segmented-cleanup sidecar + playback offset
                episode.clean_transcript_json_path,
                episode.summary_path,
                episode.playback_time_offset_seconds,
                episode.failed_at_stage,
                episode.failure_reason,
                episode.failure_type.value if episode.failure_type else None,
                episode.failed_at,
            ),
        )

    # ============================================================================
    # TranscriptLink Methods (Podcasting 2.0 <podcast:transcript> support)
    # ============================================================================

    def get_transcript_links(self, episode_id: str) -> List[TranscriptLink]:
        """Get all transcript links for an episode, oldest first."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT id, episode_id, url, mime_type, language, rel, downloaded_path, created_at
                FROM episode_transcript_links
                WHERE episode_id = %s
                ORDER BY created_at ASC
                """,
                (episode_id,),
            ).fetchall()

            return [self._row_to_transcript_link(row) for row in rows]

    def add_transcript_links(self, episode_id: str, links: List[TranscriptLink]) -> int:
        """
        Add transcript links for an episode.

        Skips duplicates (same episode_id + url). SQLite caught
        ``IntegrityError`` per row; here ``ON CONFLICT DO NOTHING`` keeps the
        transaction clean and ``rowcount`` reports whether the row inserted.

        Returns:
            Number of links actually inserted (excludes duplicates)
        """
        if not links:
            return 0

        inserted = 0
        with connect(self.dsn) as conn:
            for link in links:
                cursor = conn.execute(
                    """
                    INSERT INTO episode_transcript_links (episode_id, url, mime_type, language, rel)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (episode_id, url) DO NOTHING
                    """,
                    (
                        episode_id,
                        str(link.url),
                        link.mime_type,
                        link.language,
                        link.rel,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    # Duplicate (episode_id, url) - skip
                    logger.debug(f"Transcript link already exists: {link.url}")

        if inserted > 0:
            logger.debug(f"Added {inserted} transcript links for episode {episode_id}")

        return inserted

    def mark_transcript_downloaded(self, link_id: int, local_path: str) -> bool:
        """
        Mark a transcript link as downloaded.

        Returns True if update succeeded, False if link not found.
        """
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE episode_transcript_links
                SET downloaded_path = %s
                WHERE id = %s
                """,
                (local_path, link_id),
            )
            return cursor.rowcount > 0

    def get_episodes_with_undownloaded_transcript_links(
        self, podcast_id: Optional[str] = None
    ) -> List[Tuple[Episode, List[TranscriptLink]]]:
        """
        Get episodes that have transcript links not yet downloaded.

        Returns (Episode, undownloaded TranscriptLinks) tuples, newest first.
        Selects ``e.*`` (the SQLite version projected a partial column list
        that its own row mapper could not hydrate) via an EXISTS predicate —
        same result set as the original DISTINCT-over-JOIN.
        """
        with connect(self.dsn) as conn:
            if podcast_id:
                rows = conn.execute(
                    """
                    SELECT e.*
                    FROM episodes e
                    WHERE e.podcast_id = %s
                      AND EXISTS (SELECT 1 FROM episode_transcript_links etl
                                  WHERE etl.episode_id = e.id AND etl.downloaded_path IS NULL)
                    ORDER BY e.pub_date DESC
                    """,
                    (podcast_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT e.*
                    FROM episodes e
                    WHERE EXISTS (SELECT 1 FROM episode_transcript_links etl
                                  WHERE etl.episode_id = e.id AND etl.downloaded_path IS NULL)
                    ORDER BY e.pub_date DESC
                    """
                ).fetchall()

            results = []
            for row in rows:
                episode = self._row_to_episode(row)
                # Fetch undownloaded links for this episode
                link_rows = conn.execute(
                    """
                    SELECT id, episode_id, url, mime_type, language, rel, downloaded_path, created_at
                    FROM episode_transcript_links
                    WHERE episode_id = %s AND downloaded_path IS NULL
                    """,
                    (episode.id,),
                ).fetchall()
                links = [self._row_to_transcript_link(link_row) for link_row in link_rows]
                results.append((episode, links))

            return results

    def _row_to_transcript_link(self, row: Dict[str, Any]) -> TranscriptLink:
        """Convert database row to TranscriptLink model."""
        return TranscriptLink(
            id=row["id"],
            episode_id=as_str(row["episode_id"]),
            url=row["url"],
            mime_type=row["mime_type"],
            language=row["language"],
            rel=row["rel"],
            downloaded_path=row["downloaded_path"],
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Import (paste-a-URL) helpers
    # ------------------------------------------------------------------

    def find_episode_id_by_canonical_id(self, canonical_id: str) -> Optional[str]:
        """Return the episode UUID for a given canonical id, or None."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT id FROM episodes WHERE canonical_id = %s",
                (canonical_id,),
            ).fetchone()
            return as_str(row["id"]) if row else None

    def find_episode_id_by_audio_url(self, podcast_id: str, audio_url: str) -> Optional[str]:
        """Return the episode UUID for a given audio_url within a podcast, or None.

        Used by the import flow to attach a resolver-issued canonical_id to an
        episode already discovered via the parent's RSS feed (Apple's iTunes
        ``episodeUrl`` matches the RSS enclosure URL byte-for-byte).
        """
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT id FROM episodes WHERE podcast_id = %s AND audio_url = %s LIMIT 1",
                (podcast_id, audio_url),
            ).fetchone()
            return as_str(row["id"]) if row else None

    def set_episode_canonical_id(self, episode_id: str, canonical_id: str) -> None:
        """Stamp ``canonical_id`` on an existing episode row.

        No-op when the row already carries this canonical_id. Raises
        ``psycopg.errors.UniqueViolation`` if a different episode already owns
        the canonical_id (the unique partial index catches this).
        """
        with connect(self.dsn) as conn:
            conn.execute(
                "UPDATE episodes SET canonical_id = %s, updated_at = %s WHERE id = %s",
                (canonical_id, datetime.now(timezone.utc), episode_id),
            )

    def insert_imported_episode(
        self,
        *,
        podcast_id: str,
        canonical_id: str,
        external_id: str,
        title: str,
        audio_url: str,
        description: str = "",
        pub_date: Optional[datetime] = None,
        duration: Optional[int] = None,
        image_url: Optional[str] = None,
    ) -> str:
        """
        Insert a new imported episode and return its UUID.

        The episode is created in the ``discovered`` state so the existing
        pipeline can pick it up from the download stage. ``canonical_id`` is
        the resolver-issued typed key used for cross-user dedup.

        Raises:
            psycopg.errors.UniqueViolation: if ``canonical_id`` is already in
                use (unique partial index). Callers should look up by
                canonical_id first via ``find_episode_id_by_canonical_id``.
        """
        episode_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        # Slug must be populated for the (podcast_slug, episode_slug) URL
        # lookup to find the episode. RSS-ingested episodes get this via the
        # ``Episode`` Pydantic model's ``ensure_slug`` validator; the import
        # path skips that model so we generate the slug here.
        slug = generate_slug(title)
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    id, podcast_id, created_at, updated_at,
                    external_id, title, slug, description, description_html,
                    pub_date, audio_url, duration, image_url,
                    canonical_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '', %s, %s, %s, %s, %s)
                """,
                (
                    episode_id,
                    podcast_id,
                    now,
                    now,
                    external_id,
                    title,
                    slug,
                    description,
                    pub_date,
                    audio_url,
                    _duration_param(duration),
                    image_url,
                    canonical_id,
                ),
            )
        return episode_id
