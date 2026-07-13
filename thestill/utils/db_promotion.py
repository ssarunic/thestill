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

"""Typed data promotion: ``sqlite_mirror`` → the native Postgres schema.

Spec #44 Phase 5, step 2. Step 1 (``db_migration``) copies the SQLite DB into
Postgres as a faithful text mirror and proves parity. This module PROMOTES
that mirror into the typed public schema — text→uuid, ISO-text→timestamptz,
0/1→boolean, JSON-text→jsonb, float32-bytea→vector — almost entirely
in-database (one INSERT..SELECT with casts per table, FK dependency order).
The two embedding tables (``chunks``, ``episode_vectors``) stream through
Python to convert BLOB float32 buffers into pgvector values.

Idempotent-by-wipe: each run TRUNCATEs the typed tables first (promotion
targets a fresh cutover database, never a live one).

CLI::

    python -m thestill.utils.db_promotion promote --postgres <dsn> [--embedding-dim 384]
    python -m thestill.utils.db_promotion verify  --postgres <dsn>
"""

from __future__ import annotations

import sys
from typing import Optional

from structlog import get_logger

logger = get_logger(__name__)

MIRROR = "sqlite_mirror"

# ---------------------------------------------------------------------------
# Per-table promotion statements, FK dependency order. Conventions:
#   ts(col)    -> NULLIF(col,'')::timestamptz     (session TZ pinned to UTC)
#   b(col)     -> (NULLIF(col,'')::int::boolean)
#   j(col)     -> COALESCE(NULLIF(col,''),'<default>')::jsonb
#   uuid(col)  -> NULLIF(col,'')::uuid
# Identity targets use OVERRIDING SYSTEM VALUE to preserve original ids;
# sequences are resynced afterwards.
# ---------------------------------------------------------------------------
_PROMOTIONS: list[tuple[str, str]] = [
    (
        "users",
        """
        INSERT INTO users (id, email, name, picture, google_id, created_at, last_login_at,
                           region, region_locked, is_admin)
        SELECT id::uuid, email, name, picture, google_id,
               NULLIF(created_at,'')::timestamptz, NULLIF(last_login_at,'')::timestamptz,
               region, COALESCE(NULLIF(region_locked,'')::int::boolean,false),
               COALESCE(NULLIF(is_admin,'')::int::boolean,false)
        FROM {m}.users
        """,
    ),
    (
        "revoked_tokens",
        """
        INSERT INTO revoked_tokens (jti, expires_at, revoked_at)
        SELECT jti::uuid, NULLIF(expires_at,'')::timestamptz,
               COALESCE(NULLIF(revoked_at,'')::timestamptz, now())
        FROM {m}.revoked_tokens
        """,
    ),
    (
        "categories",
        """
        INSERT INTO categories (id, name, slug, parent_id, apple_genre_id) OVERRIDING SYSTEM VALUE
        SELECT id::bigint, name, slug, NULLIF(parent_id,'')::bigint, NULLIF(apple_genre_id,'')::bigint
        FROM {m}.categories
        """,
    ),
    (
        "podcasts",
        """
        INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, description,
            last_processed, slug, image_url, language, author, explicit, show_type, website_url,
            is_complete, copyright, etag, last_modified, primary_category_id, secondary_category_id,
            host_entity_ids, recurring_entity_ids, synthetic, auto_added, last_processed_at,
            refresh_interval_seconds, next_refresh_at, last_refresh_at, last_refresh_error)
        SELECT id::uuid, NULLIF(created_at,'')::timestamptz, NULLIF(updated_at,'')::timestamptz,
               rss_url, title, COALESCE(description,''),
               NULLIF(last_processed,'')::timestamptz, COALESCE(slug,''), image_url,
               COALESCE(NULLIF(language,''),'en'), author, NULLIF(explicit,'')::int::boolean,
               show_type, website_url, COALESCE(NULLIF(is_complete,'')::int::boolean,false),
               copyright, etag, last_modified,
               NULLIF(primary_category_id,'')::bigint, NULLIF(secondary_category_id,'')::bigint,
               COALESCE(NULLIF(host_entity_ids,''),'[]')::jsonb,
               COALESCE(NULLIF(recurring_entity_ids,''),'[]')::jsonb,
               COALESCE(NULLIF(synthetic,'')::int::boolean,false),
               COALESCE(NULLIF(auto_added,'')::int::boolean,false),
               NULLIF(last_processed_at,'')::timestamptz,
               NULLIF(refresh_interval_seconds,'')::bigint,
               NULLIF(next_refresh_at,'')::timestamptz, NULLIF(last_refresh_at,'')::timestamptz,
               last_refresh_error
        FROM {m}.podcasts
        """,
    ),
    (
        "episodes",
        """
        INSERT INTO episodes (id, podcast_id, created_at, updated_at, external_id, title,
            description, pub_date, audio_url, duration, audio_path, downsampled_audio_path,
            raw_transcript_path, clean_transcript_path, summary_path, slug, image_url,
            failed_at_stage, failure_reason, failure_type, failed_at, description_html, explicit,
            episode_type, episode_number, season_number, website_url, audio_file_size,
            audio_mime_type, clean_transcript_json_path, playback_time_offset_seconds,
            entity_extraction_status, guest_entity_ids, published_at, canonical_id,
            auto_process_excluded)
        SELECT id::uuid, podcast_id::uuid, NULLIF(created_at,'')::timestamptz,
               NULLIF(updated_at,'')::timestamptz, external_id, title, COALESCE(description,''),
               NULLIF(pub_date,'')::timestamptz, audio_url, duration, audio_path,
               downsampled_audio_path, raw_transcript_path, clean_transcript_path, summary_path,
               COALESCE(slug,''), image_url, failed_at_stage, failure_reason, failure_type,
               NULLIF(failed_at,'')::timestamptz, COALESCE(description_html,''),
               NULLIF(explicit,'')::int::boolean, episode_type,
               NULLIF(episode_number,'')::bigint, NULLIF(season_number,'')::bigint, website_url,
               NULLIF(audio_file_size,'')::bigint, audio_mime_type, clean_transcript_json_path,
               COALESCE(NULLIF(playback_time_offset_seconds,'')::double precision, 0.0),
               entity_extraction_status, COALESCE(NULLIF(guest_entity_ids,''),'[]')::jsonb,
               NULLIF(published_at,'')::timestamptz, canonical_id,
               COALESCE(NULLIF(auto_process_excluded,'')::int::boolean,false)
        FROM {m}.episodes
        """,
    ),
    (
        "episode_alternate_enclosures",
        """
        INSERT INTO episode_alternate_enclosures (id, episode_id, source_uri, mime_type, length,
            bitrate, height, title, rel, language, is_default, created_at) OVERRIDING SYSTEM VALUE
        SELECT id::bigint, episode_id::uuid, source_uri, mime_type, NULLIF(length,'')::bigint,
               NULLIF(bitrate,'')::double precision, NULLIF(height,'')::bigint, title, rel,
               language, COALESCE(NULLIF(is_default,'')::int::boolean,false),
               NULLIF(created_at,'')::timestamptz
        FROM {m}.episode_alternate_enclosures
        """,
    ),
    (
        "episode_transcript_links",
        """
        INSERT INTO episode_transcript_links (id, episode_id, url, mime_type, language, rel,
            downloaded_path, created_at) OVERRIDING SYSTEM VALUE
        SELECT id::bigint, episode_id::uuid, url, mime_type, language, rel, downloaded_path,
               NULLIF(created_at,'')::timestamptz
        FROM {m}.episode_transcript_links
        """,
    ),
    (
        "podcast_followers",
        """
        INSERT INTO podcast_followers (id, user_id, podcast_id, created_at)
        SELECT id::uuid, user_id::uuid, podcast_id::uuid, NULLIF(created_at,'')::timestamptz
        FROM {m}.podcast_followers
        """,
    ),
    (
        "user_episode_inbox",
        """
        INSERT INTO user_episode_inbox (id, user_id, episode_id, source, state, delivered_at,
            state_changed_at)
        SELECT id::uuid, user_id::uuid, episode_id::uuid, source, state,
               NULLIF(delivered_at,'')::timestamptz, NULLIF(state_changed_at,'')::timestamptz
        FROM {m}.user_episode_inbox
        """,
    ),
    (
        "user_briefings",
        """
        INSERT INTO user_briefings (id, user_id, cursor_from, cursor_to, episode_count,
            script_path, audio_path, created_at, listened_at)
        SELECT id::uuid, user_id::uuid, NULLIF(cursor_from,'')::timestamptz,
               NULLIF(cursor_to,'')::timestamptz, COALESCE(NULLIF(episode_count,'')::bigint,0),
               script_path, audio_path, NULLIF(created_at,'')::timestamptz,
               NULLIF(listened_at,'')::timestamptz
        FROM {m}.user_briefings
        """,
    ),
    (
        "tasks",
        """
        INSERT INTO tasks (id, episode_id, podcast_id, stage, status, priority, error_message,
            created_at, updated_at, started_at, completed_at, retry_count, max_retries,
            next_retry_at, error_type, last_error, metadata, error_class, heal_attempts,
            last_heal_at)
        SELECT id::uuid, NULLIF(episode_id,'')::uuid, NULLIF(podcast_id,'')::uuid, stage, status,
               COALESCE(NULLIF(priority,'')::bigint,0), error_message,
               NULLIF(created_at,'')::timestamptz, NULLIF(updated_at,'')::timestamptz,
               NULLIF(started_at,'')::timestamptz, NULLIF(completed_at,'')::timestamptz,
               COALESCE(NULLIF(retry_count,'')::bigint,0), COALESCE(NULLIF(max_retries,'')::bigint,3),
               NULLIF(next_retry_at,'')::timestamptz, error_type, last_error,
               NULLIF(metadata,'')::jsonb, error_class, COALESCE(NULLIF(heal_attempts,'')::bigint,0),
               NULLIF(last_heal_at,'')::timestamptz
        FROM {m}.tasks
        """,
    ),
    (
        "pending_transcription_operations",
        """
        INSERT INTO pending_transcription_operations (operation_id, provider, episode_id,
            payload_json, created_at, updated_at)
        SELECT operation_id, provider, episode_id::uuid,
               COALESCE(NULLIF(payload_json,''),'{{}}')::jsonb,
               NULLIF(created_at,'')::timestamptz, NULLIF(updated_at,'')::timestamptz
        FROM {m}.pending_transcription_operations
        """,
    ),
    (
        "entities",
        """
        INSERT INTO entities (id, type, canonical_name, wikidata_qid, aliases, description,
            created_at, updated_at, wikidata_instance_of)
        SELECT id, type, canonical_name, wikidata_qid, COALESCE(NULLIF(aliases,''),'[]')::jsonb,
               description, NULLIF(created_at,'')::timestamptz, NULLIF(updated_at,'')::timestamptz,
               COALESCE(NULLIF(wikidata_instance_of,''),'[]')::jsonb
        FROM {m}.entities
        """,
    ),
    (
        "entity_mentions",
        """
        INSERT INTO entity_mentions (id, entity_id, resolution_status, episode_id, segment_id,
            start_ms, end_ms, speaker, role, surface_form, surface_label, quote_excerpt,
            sentiment, confidence, extractor, resolution_method, candidate_entity_ids,
            created_at, resolved_at) OVERRIDING SYSTEM VALUE
        SELECT id::bigint, NULLIF(entity_id,''), resolution_status, episode_id::uuid,
               segment_id::bigint, start_ms::bigint, end_ms::bigint, speaker, role, surface_form,
               surface_label, quote_excerpt, NULLIF(sentiment,'')::double precision,
               confidence::double precision, extractor, resolution_method,
               NULLIF(candidate_entity_ids,'')::jsonb,
               NULLIF(created_at,'')::timestamptz, NULLIF(resolved_at,'')::timestamptz
        FROM {m}.entity_mentions
        """,
    ),
    (
        "entity_cooccurrences",
        """
        INSERT INTO entity_cooccurrences (entity_a_id, entity_b_id, episode_count, last_seen_at)
        SELECT entity_a_id, entity_b_id, episode_count::bigint, NULLIF(last_seen_at,'')::timestamptz
        FROM {m}.entity_cooccurrences
        """,
    ),
    (
        "entity_enrichment",
        """
        INSERT INTO entity_enrichment (entity_id, image_url, image_attribution, image_license,
            headline, wikipedia_extract, wikipedia_url, facts_json, affiliations_json,
            wikidata_status, wikidata_fetched_at, wikipedia_status, wikipedia_fetched_at,
            retry_after, schema_version, created_at, updated_at)
        SELECT entity_id, image_url, image_attribution, image_license, headline,
               wikipedia_extract, wikipedia_url, COALESCE(NULLIF(facts_json,''),'[]')::jsonb,
               COALESCE(NULLIF(affiliations_json,''),'[]')::jsonb, wikidata_status,
               NULLIF(wikidata_fetched_at,'')::timestamptz, wikipedia_status,
               NULLIF(wikipedia_fetched_at,'')::timestamptz, NULLIF(retry_after,'')::timestamptz,
               COALESCE(NULLIF(schema_version,'')::bigint,1),
               NULLIF(created_at,'')::timestamptz, NULLIF(updated_at,'')::timestamptz
        FROM {m}.entity_enrichment
        """,
    ),
    (
        "mention_overrides",
        """
        INSERT INTO mention_overrides (id, surface_form, episode_id, override_kind, entity_id,
            reason, created_by, created_at) OVERRIDING SYSTEM VALUE
        SELECT id::bigint, surface_form, NULLIF(episode_id,'')::uuid, override_kind,
               NULLIF(entity_id,''), reason, created_by, NULLIF(created_at,'')::timestamptz
        FROM {m}.mention_overrides
        """,
    ),
    (
        "resolution_blacklist",
        """
        INSERT INTO resolution_blacklist (id, surface_form, wrong_qid, reason, created_at)
            OVERRIDING SYSTEM VALUE
        SELECT id::bigint, surface_form, wrong_qid, reason, NULLIF(created_at,'')::timestamptz
        FROM {m}.resolution_blacklist
        """,
    ),
    (
        "episode_related",
        """
        INSERT INTO episode_related (episode_id, related_episode_id, rank, score, computed_at)
        SELECT episode_id::uuid, related_episode_id::uuid, rank::bigint,
               score::double precision, NULLIF(computed_at,'')::timestamptz
        FROM {m}.episode_related
        """,
    ),
    (
        "related_idf",
        "INSERT INTO related_idf (term, idf) SELECT term, idf::double precision FROM {m}.related_idf",
    ),
    (
        "top_podcasts",
        """
        INSERT INTO top_podcasts (id, name, artist, rss_url, apple_url, youtube_url,
            apple_track_id, image_url, category_id, first_seen_at, last_seen_at) OVERRIDING SYSTEM VALUE
        SELECT id::bigint, name, artist, rss_url, apple_url, youtube_url, apple_track_id,
               image_url, NULLIF(category_id,'')::bigint, NULLIF(first_seen_at,'')::timestamptz,
               NULLIF(last_seen_at,'')::timestamptz
        FROM {m}.top_podcasts
        """,
    ),
    (
        "top_podcast_rankings",
        """
        INSERT INTO top_podcast_rankings (top_podcast_id, region, rank, source_genre, scraped_at)
        SELECT top_podcast_id::bigint, region, rank::bigint, source_genre,
               NULLIF(scraped_at,'')::timestamptz
        FROM {m}.top_podcast_rankings
        """,
    ),
    (
        "top_podcasts_meta",
        """
        INSERT INTO top_podcasts_meta (region, source_path, source_mtime, row_count, seeded_at)
        SELECT region, source_path, source_mtime::double precision, row_count::bigint,
               NULLIF(seeded_at,'')::timestamptz
        FROM {m}.top_podcasts_meta
        """,
    ),
]

# Identity tables whose sequences must be resynced after OVERRIDING inserts.
_IDENTITY_TABLES = [
    "categories",
    "episode_alternate_enclosures",
    "episode_transcript_links",
    "entity_mentions",
    "mention_overrides",
    "resolution_blacklist",
    "top_podcasts",
    "chunks",
]

_ALL_TYPED = [t for t, _ in _PROMOTIONS] + ["chunks", "episode_vectors"]


# HNSW indexes are dropped for the bulk vector load and rebuilt afterwards:
# maintaining the graph per-row made the load the dominant cost of the whole
# promotion (~35 min for 260k vectors); a deferred build is minutes. This is
# the standard pgvector bulk-load recipe.
_VECTOR_INDEXES = {
    "idx_chunks_embedding_hnsw": "CREATE INDEX idx_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)",
    "idx_episode_vectors_hnsw": "CREATE INDEX idx_episode_vectors_hnsw ON episode_vectors USING hnsw (centroid vector_cosine_ops)",
}

_VECTOR_BATCH = 2000


def _promote_vectors(pconn, dim: int) -> dict[str, int]:
    """Stream chunks + episode_vectors: float32 bytea → pgvector values.

    Batched ``executemany`` (psycopg pipelines it) with the HNSW indexes
    dropped by the caller — per-row graph maintenance is what made the naive
    loop slow.
    """
    import numpy as np
    from pgvector.psycopg import register_vector

    register_vector(pconn)
    counts = {}

    def _flush(write, sql, batch):
        if batch:
            write.executemany(sql, batch)
            batch.clear()

    chunk_sql = """
        INSERT INTO chunks (id, episode_id, segment_id, start_ms, end_ms, speaker, text,
            embedding_model, embedding, created_at) OVERRIDING SYSTEM VALUE
        VALUES (%s, %s::uuid, %s::bigint, %s::bigint, %s::bigint, %s, %s, %s, %s,
                NULLIF(%s,'')::timestamptz)
    """
    with pconn.cursor(name="mirror_chunks") as read, pconn.cursor() as write:
        read.execute(
            f"SELECT id, episode_id, segment_id, start_ms, end_ms, speaker, text, "
            f"embedding_model, embedding, created_at FROM {MIRROR}.chunks"
        )
        n = 0
        batch: list = []
        for row in read:
            emb = np.frombuffer(bytes(row[8]), dtype=np.float32)
            if emb.shape[0] != dim:
                logger.warning("chunk_embedding_dim_mismatch", chunk_id=row[0], got=emb.shape[0])
                continue
            batch.append((int(row[0]), row[1], row[2], row[3], row[4], row[5], row[6], row[7], emb, row[9]))
            n += 1
            if len(batch) >= _VECTOR_BATCH:
                _flush(write, chunk_sql, batch)
        _flush(write, chunk_sql, batch)
    counts["chunks"] = n

    ev_sql = """
        INSERT INTO episode_vectors (episode_id, embedding_model, chunk_count, centroid,
            computed_at)
        VALUES (%s::uuid, %s, %s::bigint, %s, NULLIF(%s,'')::timestamptz)
    """
    with pconn.cursor(name="mirror_ev") as read, pconn.cursor() as write:
        read.execute(
            f"SELECT episode_id, embedding_model, chunk_count, centroid, computed_at " f"FROM {MIRROR}.episode_vectors"
        )
        n = 0
        batch = []
        for row in read:
            cent = np.frombuffer(bytes(row[3]), dtype=np.float32)
            if cent.shape[0] != dim:
                logger.warning("centroid_dim_mismatch", episode_id=row[0], got=cent.shape[0])
                continue
            batch.append((row[0], row[1], row[2], cent, row[4]))
            n += 1
            if len(batch) >= _VECTOR_BATCH:
                _flush(write, ev_sql, batch)
        _flush(write, ev_sql, batch)
    counts["episode_vectors"] = n
    return counts


def promote(dsn: str, *, embedding_dim: int = 384) -> dict[str, int]:
    """Run the full promotion. Returns {table: rows_promoted}."""
    import psycopg

    from ..repositories.postgres_schema import ensure_schema

    ensure_schema(dsn, embedding_dim=embedding_dim)
    counts: dict[str, int] = {}

    with psycopg.connect(dsn) as conn:
        conn.execute("SET timezone = 'UTC'")
        # Wipe typed targets (reverse FK order via CASCADE on the roots).
        conn.execute(f"TRUNCATE {', '.join(_ALL_TYPED)} CASCADE")

        for table, sql in _PROMOTIONS:
            cur = conn.execute(sql.format(m=MIRROR))
            counts[table] = cur.rowcount or 0
            logger.info("promoted_table", table=table, rows=counts[table])

        # Deferred HNSW builds: drop -> bulk load -> recreate (see
        # _VECTOR_INDEXES). Same transaction, so a failure rolls back to a
        # consistent pre-promotion state, indexes included.
        for index_name in _VECTOR_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {index_name}")

        counts.update(_promote_vectors(conn, embedding_dim))

        for index_name, create_sql in _VECTOR_INDEXES.items():
            logger.info("rebuilding_vector_index", index=index_name)
            conn.execute(create_sql)

        # Resync identity sequences past the preserved ids.
        for table in _IDENTITY_TABLES:
            conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}','id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
            )
    return counts


def verify(dsn: str) -> list[tuple[str, int, int, bool]]:
    """Row-count parity: mirror vs typed, per table. Returns (table, mirror, typed, ok)."""
    import psycopg

    out = []
    with psycopg.connect(dsn) as conn:
        for table in _ALL_TYPED:
            m = conn.execute(f'SELECT COUNT(*) FROM {MIRROR}."{table}"').fetchone()[0]
            t = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            out.append((table, m, t, m == t))
    return out


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Promote sqlite_mirror → typed Postgres schema (spec #44)")
    parser.add_argument("mode", choices=["promote", "verify"])
    parser.add_argument("--postgres", required=True)
    parser.add_argument("--embedding-dim", type=int, default=384)
    args = parser.parse_args(argv)
    out = sys.stdout.write

    if args.mode == "promote":
        counts = promote(args.postgres, embedding_dim=args.embedding_dim)
        out(f"Promoted {len(counts)} tables, {sum(counts.values())} rows.\n")

    rows = verify(args.postgres)
    ok = True
    for table, m, t, match in rows:
        mark = "ok " if match else "MISMATCH"
        ok = ok and match
        out(f"  [{mark}] {table:34} mirror={m} typed={t}\n")
    out("PROMOTION OK\n" if ok else "PROMOTION FAILED\n")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
