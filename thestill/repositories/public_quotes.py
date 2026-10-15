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

"""Quotes for the public person, entity and episode pages (spec #94).

A **public quote** is a resolved entity mention (a person, company, product
or topic named in a transcript segment) whose segment was *spoken by a named
person*: the segment's synthesized ``speaking`` row resolves to a ``person``
entity. The mention's ``quote_excerpt`` (about 200 characters of the real
sentence around the name) is the quote; the speaking row's entity is who
said it; the mention's entity is what it is about.

This gives every public quote an attributed speaker by construction, so
"SPEAKER_1", ``None`` and unnamed speakers can never appear. A light
read-time gate stands in for spec #100's candidates table: at least 60
characters, not in the first 90 seconds (intros and housekeeping), and no
obvious sponsor wording. One row per transcript segment.

The SQL is shared by both backends (``{p}`` is the driver placeholder), the
same pattern as :data:`entity_repository.RECENT_MENTIONS_BY_EPISODE_SQL`, so
the gate cannot drift between SQLite and Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Lower-cased fragments that mark ad reads and show housekeeping.
SPONSOR_TERMS: Tuple[str, ...] = (
    "promo code",
    "use code",
    "brought to you by",
    "sponsored by",
    "our sponsor",
    "this episode is supported",
    "dot com slash",
    ".com/",
    "free trial",
    "% off",
    "subscribe to",
    "leave us a review",
    "send us a voice",
)
MIN_QUOTE_CHARS = 60
INTRO_SKIP_MS = 90_000


def _gate(p: str) -> Tuple[str, List[Any]]:
    """WHERE fragment shared by every public quote query, plus its params."""
    sponsor = " ".join(f"AND lower(m.quote_excerpt) NOT LIKE {p}" for _ in SPONSOR_TERMS)
    sql = f"""
        m.resolution_status = 'resolved'
        AND m.entity_id IS NOT NULL
        AND (m.role IS NULL OR m.role <> 'speaking')
        AND m.entity_id <> sp.entity_id
        AND length(m.quote_excerpt) >= {MIN_QUOTE_CHARS}
        AND m.start_ms >= {INTRO_SKIP_MS}
        {sponsor}
        AND p.public_pages AND NOT COALESCE(p.synthetic, FALSE)
        AND p.slug <> '' AND e.slug <> ''
        AND e.summary_path IS NOT NULL AND COALESCE(e.summary_preview, '') <> ''
        AND NOT spe.public_hidden AND NOT ae.public_hidden
    """
    return sql, [f"%{term}%" for term in SPONSOR_TERMS]


_FROM = """
    FROM entity_mentions m
    JOIN entity_mentions sp
      ON sp.episode_id = m.episode_id AND sp.segment_id = m.segment_id
     AND sp.role = 'speaking' AND sp.resolution_status = 'resolved' AND sp.entity_id IS NOT NULL
    JOIN entities spe ON spe.id = sp.entity_id AND spe.type = 'person'
    JOIN entities ae ON ae.id = m.entity_id
    JOIN episodes e ON e.id = m.episode_id
    JOIN podcasts p ON p.id = e.podcast_id
"""

_COLUMNS = """
    m.id AS mention_id, m.episode_id, m.segment_id, m.start_ms, m.quote_excerpt AS quote,
    sp.entity_id AS speaker_id, spe.canonical_name AS speaker_name,
    m.entity_id AS about_id, ae.canonical_name AS about_name, ae.type AS about_type,
    e.slug AS episode_slug, e.title AS episode_title, e.pub_date AS episode_pub_date,
    e.image_url AS episode_image_url,
    p.slug AS podcast_slug, p.title AS podcast_title, p.image_url AS podcast_image_url
"""


@dataclass(frozen=True)
class QuoteFilter:
    """Which quotes to select. At least one field must be set."""

    speaker_id: Optional[str] = None  # quotes this person said
    about_id: Optional[str] = None  # quotes that name this entity
    exclude_speaker_id: Optional[str] = None  # ...said by someone other than this person
    episode_id: Optional[str] = None  # quotes in this episode
    since: Optional[datetime] = None  # episodes published on/after this time


def _where(f: QuoteFilter, p: str) -> Tuple[str, List[Any]]:
    gate_sql, params = _gate(p)
    clauses = [gate_sql]
    if f.speaker_id:
        clauses.append(f"sp.entity_id = {p}")
        params.append(f.speaker_id)
    if f.about_id:
        clauses.append(f"m.entity_id = {p}")
        params.append(f.about_id)
    if f.exclude_speaker_id:
        clauses.append(f"sp.entity_id <> {p}")
        params.append(f.exclude_speaker_id)
    if f.episode_id:
        clauses.append(f"m.episode_id = {p}")
        params.append(f.episode_id)
    if f.since:
        clauses.append(f"e.pub_date >= {p}")
        # ISO text with a "T" compares correctly against SQLite's stored
        # strings and casts cleanly to timestamptz on Postgres.
        params.append(f.since.isoformat())
    return " AND ".join(f"({c})" for c in clauses), params


def quotes_sql(f: QuoteFilter, p: str, *, order: str, limit: int, offset: int) -> Tuple[str, List[Any]]:
    """Quotes matching ``f``: ``order`` is ``recent`` (newest episode, then
    time in episode), ``episode`` (time in episode) or ``longest``."""
    where, params = _where(f, p)
    order_sql = {
        "recent": "q.episode_pub_date DESC, q.episode_id, q.start_ms",
        "episode": "q.start_ms",
        "longest": "length(q.quote) DESC, q.episode_pub_date DESC",
    }[order]
    sql = f"""
        WITH ranked AS (
            SELECT {_COLUMNS},
                   ROW_NUMBER() OVER (PARTITION BY m.episode_id, m.segment_id ORDER BY m.id) AS segment_rank
            {_FROM}
            WHERE {where}
        )
        SELECT q.*, COUNT(*) OVER () AS total_count
        FROM ranked q
        WHERE q.segment_rank = 1
        ORDER BY {order_sql}
        LIMIT {p} OFFSET {p}
    """
    return sql, [*params, limit, offset]


def speakers_sql(about_id: str, p: str, *, since: Optional[datetime], limit: int) -> Tuple[str, List[Any]]:
    """Who's talking about ``about_id``: named speakers by quote count."""
    where, params = _where(QuoteFilter(about_id=about_id, exclude_speaker_id=about_id, since=since), p)
    sql = f"""
        SELECT sp.entity_id AS speaker_id, spe.canonical_name AS speaker_name,
               COUNT(DISTINCT m.episode_id || ':' || CAST(m.segment_id AS TEXT)) AS quotes,
               COUNT(DISTINCT p.id) AS shows
        {_FROM}
        WHERE {where}
        GROUP BY sp.entity_id, spe.canonical_name
        ORDER BY quotes DESC, speaker_name
        LIMIT {p}
    """
    return sql, [*params, limit]


def topics_sql(speaker_id: str, p: str, *, limit: int) -> Tuple[str, List[Any]]:
    """What ``speaker_id`` talks about: the entities their quotes name."""
    where, params = _where(QuoteFilter(speaker_id=speaker_id), p)
    sql = f"""
        SELECT m.entity_id AS about_id, ae.canonical_name AS about_name, ae.type AS about_type,
               COUNT(DISTINCT m.episode_id || ':' || CAST(m.segment_id AS TEXT)) AS quotes
        {_FROM}
        WHERE {where}
        GROUP BY m.entity_id, ae.canonical_name, ae.type
        ORDER BY quotes DESC, about_name
        LIMIT {p}
    """
    return sql, [*params, limit]


def trending_sql(p: str, *, since: datetime, limit: int) -> Tuple[str, List[Any]]:
    """Things (not people) the most distinct named speakers discussed since
    ``since``, across the most shows: the landing's "Who said what"."""
    where, params = _where(QuoteFilter(since=since), p)
    sql = f"""
        SELECT m.entity_id AS about_id, ae.canonical_name AS about_name, ae.type AS about_type,
               COUNT(DISTINCT sp.entity_id) AS speakers, COUNT(DISTINCT p.id) AS shows
        {_FROM}
        WHERE {where} AND ae.type <> 'person'
        GROUP BY m.entity_id, ae.canonical_name, ae.type
        HAVING COUNT(DISTINCT sp.entity_id) >= 2
        ORDER BY shows DESC, speakers DESC, about_name
        LIMIT {p}
    """
    return sql, [*params, limit]


def indexable_entities_sql(p: str, *, min_quotes: int) -> Tuple[str, List[Any]]:
    """Entities whose public page has enough quotes to be worth indexing:
    people by quotes said plus quotes about them, things by quotes about
    them. ``(entity_id, entity_type, quotes)`` rows."""
    gate_sql, params = _gate(p)
    sql = f"""
        WITH q AS (
            SELECT DISTINCT m.episode_id || ':' || CAST(m.segment_id AS TEXT) AS seg,
                   sp.entity_id AS speaker_id, m.entity_id AS about_id
            {_FROM}
            WHERE {gate_sql}
        ),
        -- Distinct segments, as the page counts them: a segment naming two
        -- things is one quote by its speaker, not two.
        counts AS (
            SELECT speaker_id AS entity_id, COUNT(DISTINCT seg) AS n FROM q GROUP BY speaker_id
            UNION ALL
            SELECT about_id AS entity_id, COUNT(DISTINCT seg) AS n FROM q GROUP BY about_id
        )
        SELECT c.entity_id, ent.type AS entity_type, SUM(c.n) AS quotes
        FROM counts c JOIN entities ent ON ent.id = c.entity_id
        GROUP BY c.entity_id, ent.type
        HAVING SUM(c.n) >= {p}
        ORDER BY c.entity_id
    """
    return sql, [*params, min_quotes]


def presence_sql(entity_id: str, p: str) -> Tuple[str, List[Any]]:
    """Public episodes where the entity speaks or is named: ``(episode_id,
    podcast_id, pub_date)`` rows for the hero counts and the activity chart."""
    sql = f"""
        SELECT DISTINCT e.id AS episode_id, p.id AS podcast_id, e.pub_date AS pub_date
        FROM entity_mentions m
        JOIN episodes e ON e.id = m.episode_id
        JOIN podcasts p ON p.id = e.podcast_id
        WHERE m.entity_id = {p} AND m.resolution_status = 'resolved'
          AND p.public_pages AND NOT COALESCE(p.synthetic, FALSE)
          AND e.summary_path IS NOT NULL AND COALESCE(e.summary_preview, '') <> ''
    """
    return sql, [entity_id]


Fetch = Callable[[str, Sequence[Any]], List[Dict[str, Any]]]


class PublicQuoteQueries:
    """Runs the public quote SQL through a backend's ``fetch(sql, params)``.

    ``placeholder`` is ``%s`` for Postgres and ``?`` for SQLite.
    """

    def __init__(self, fetch: Fetch, placeholder: str) -> None:
        self._fetch = fetch
        self._p = placeholder

    def quotes(
        self, f: QuoteFilter, *, order: str = "recent", limit: int = 10, offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        sql, params = quotes_sql(f, self._p, order=order, limit=limit, offset=offset)
        rows = self._fetch(sql, params)
        total = int(rows[0]["total_count"]) if rows else 0
        return rows, total

    def speakers(self, about_id: str, *, since: Optional[datetime] = None, limit: int = 8) -> List[Dict[str, Any]]:
        sql, params = speakers_sql(about_id, self._p, since=since, limit=limit)
        return self._fetch(sql, params)

    def topics(self, speaker_id: str, *, limit: int = 8) -> List[Dict[str, Any]]:
        sql, params = topics_sql(speaker_id, self._p, limit=limit)
        return self._fetch(sql, params)

    def trending(self, *, since: datetime, limit: int = 1) -> List[Dict[str, Any]]:
        sql, params = trending_sql(self._p, since=since, limit=limit)
        return self._fetch(sql, params)

    def indexable_entities(self, *, min_quotes: int = 3) -> List[Dict[str, Any]]:
        sql, params = indexable_entities_sql(self._p, min_quotes=min_quotes)
        return self._fetch(sql, params)

    def presence(self, entity_id: str) -> List[Dict[str, Any]]:
        sql, params = presence_sql(entity_id, self._p)
        return self._fetch(sql, params)


def days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)
