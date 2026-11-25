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

"""Repository for quote shares, the short links of spec #114.

One class holds the SQL for both backends (FM-6: one definition); the
SQLite and Postgres subclasses supply the connection, the placeholder and
how a timestamp and a day are bound.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence


def _comparable(text: str) -> str:
    """Text as the speaker re-check compares it: whitespace collapsed, lower case."""
    return " ".join(text.split()).lower()


_INSERT_COLUMNS = (
    "id",
    "episode_id",
    "podcast_id",
    "segment_id",
    "start_ms",
    "end_ms",
    "text",
    "speaker_entity_id",
    "source",
)
#: Where a share's quote came from: a transcript excerpt (spec #114) or the
#: summary's Best Quotes (spec #120). Only ``gated`` shares are re-anchored
#: by their words after an entity refresh; a summary share is re-minted with
#: its current speaker whenever the episode's quotes are built.
SOURCES = ("gated", "summary")


def _insert_value(share: Dict[str, Any], column: str) -> Any:
    if column == "source":
        return share.get("source") or "gated"
    return share[column]


class QuoteShareRepository(ABC):
    """``quote_shares`` and ``quote_share_hits``."""

    #: The parameter placeholder: ``?`` or ``%s``.
    placeholder: str

    @abstractmethod
    def _fetch(self, sql: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
        """Run a read and return its rows as dicts."""

    @abstractmethod
    def _execute(self, sql: str, params: Sequence[Any]) -> int:
        """Run one write and return the number of rows it changed."""

    @abstractmethod
    def _executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        """Run one write per row, in one transaction."""

    @abstractmethod
    def _timestamp(self, moment: datetime) -> Any:
        """``moment`` as this backend stores a timestamp."""

    @abstractmethod
    def _day(self, day: date) -> Any:
        """``day`` as this backend stores a date."""

    # ----- minting -----

    def upsert_gated(self, shares: Iterable[Dict[str, Any]]) -> Dict[str, bool]:
        """Persist gated shares and say which ids may be shown.

        A new id is inserted. An id that already holds the same quote
        (episode, start and words) has its cached speaker and segment
        refreshed. An id that holds a *different* quote is a hash collision
        and is left alone. Returns ``{id: usable}``: usable means the row
        now describes this quote and is not revoked.
        """
        # In id order: two payloads minting overlapping sets at once then
        # take their row locks in the same order and cannot deadlock.
        shares = sorted(shares, key=lambda share: share["id"])
        if not shares:
            return {}
        p = self.placeholder
        sql = f"""
            INSERT INTO quote_shares ({", ".join(_INSERT_COLUMNS)})
            VALUES ({", ".join(p for _ in _INSERT_COLUMNS)})
            ON CONFLICT (id) DO UPDATE SET
                speaker_entity_id = EXCLUDED.speaker_entity_id,
                segment_id = EXCLUDED.segment_id,
                end_ms = EXCLUDED.end_ms
            WHERE quote_shares.episode_id = EXCLUDED.episode_id
              AND quote_shares.start_ms = EXCLUDED.start_ms
              AND quote_shares.text = EXCLUDED.text
              AND (quote_shares.speaker_entity_id IS NOT EXCLUDED.speaker_entity_id
                   OR quote_shares.segment_id <> EXCLUDED.segment_id
                   OR quote_shares.end_ms <> EXCLUDED.end_ms)
        """.replace(
            "IS NOT EXCLUDED", self._is_distinct_from
        )
        self._executemany(sql, [tuple(_insert_value(share, column) for column in _INSERT_COLUMNS) for share in shares])
        wanted = {share["id"]: share for share in shares}
        marks = ", ".join(p for _ in wanted)
        rows = self._fetch(
            f"SELECT id, episode_id, start_ms, text, revoked_at FROM quote_shares WHERE id IN ({marks})",
            list(wanted),
        )
        usable = {share_id: False for share_id in wanted}
        for row in rows:
            share = wanted[row["id"]]
            usable[row["id"]] = (
                row["revoked_at"] is None
                and str(row["episode_id"]) == str(share["episode_id"])
                and int(row["start_ms"]) == int(share["start_ms"])
                and row["text"] == share["text"]
            )
        return usable

    #: ``a IS NOT b`` treating NULL as a value; Postgres spells it differently.
    _is_distinct_from = "IS NOT EXCLUDED"

    # ----- reads -----

    def get(self, share_id: str) -> Optional[Dict[str, Any]]:
        """One share with what a card and its landing need, or None.

        The speaker is the row's cached entity, followed through one
        ``entity_redirects`` hop when a merge deleted it.
        """
        p = self.placeholder
        rows = self._fetch(
            f"""
            SELECT s.id, s.episode_id, s.podcast_id, s.segment_id, s.start_ms, s.end_ms, s.text,
                   s.speaker_entity_id, s.source, s.created_at, s.revoked_at, s.revoked_reason,
                   s.template_version,
                   e.slug AS episode_slug, e.title AS episode_title, e.image_url AS episode_image_url,
                   (e.summary_path IS NOT NULL AND COALESCE(e.summary_preview, '') <> '') AS episode_public,
                   p.slug AS podcast_slug, p.title AS podcast_title, p.image_url AS podcast_image_url,
                   (p.public_pages AND NOT COALESCE(p.synthetic, FALSE)) AS podcast_public,
                   sp.id AS speaker_id, sp.canonical_name AS speaker_name,
                   COALESCE(sp.public_hidden, FALSE) AS speaker_hidden
            FROM quote_shares s
            JOIN episodes e ON e.id = s.episode_id
            JOIN podcasts p ON p.id = s.podcast_id
            LEFT JOIN entity_redirects r ON r.from_entity_id = s.speaker_entity_id
            LEFT JOIN entities sp ON sp.id = COALESCE(r.to_entity_id, s.speaker_entity_id)
            WHERE s.id = {p}
            """,
            [share_id],
        )
        return rows[0] if rows else None

    def list_for_episode(self, episode_id: str) -> List[Dict[str, Any]]:
        """Every share of an episode, in episode order, with its opens."""
        p = self.placeholder
        return self._fetch(
            f"""
            SELECT s.id, s.start_ms, s.text, s.speaker_entity_id, s.created_at, s.revoked_at, s.revoked_reason,
                   COALESCE((SELECT SUM(h.hits) FROM quote_share_hits h WHERE h.share_id = s.id), 0) AS opens
            FROM quote_shares s
            WHERE s.episode_id = {p}
            ORDER BY s.start_ms, s.id
            """,
            [episode_id],
        )

    def live_for_episode(self, episode_id: str) -> List[Dict[str, Any]]:
        """The episode's shares that are not revoked, with their anchors."""
        p = self.placeholder
        return self._fetch(
            f"SELECT id, segment_id, start_ms, end_ms, text, speaker_entity_id, source FROM quote_shares "
            f"WHERE episode_id = {p} AND revoked_at IS NULL ORDER BY id",
            [episode_id],
        )

    def ids_for_speaker(self, entity_id: str) -> List[str]:
        """Every share that names this entity: cached as it, or as an
        entity that was merged into it (``get`` follows the same redirect)."""
        p = self.placeholder
        return [
            row["id"]
            for row in self._fetch(
                f"""
                SELECT id FROM quote_shares
                WHERE speaker_entity_id = {p}
                   OR speaker_entity_id IN (SELECT from_entity_id FROM entity_redirects WHERE to_entity_id = {p})
                ORDER BY id
                """,
                [entity_id, entity_id],
            )
        ]

    def speakers_of_passage(self, episode_id: str, text: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
        """Who speaks ``text`` inside the source span now (spec #114 §7).

        A share's text is its mention's excerpt, trimmed to whole sentences
        for the card (``card_text``): first letter possibly capitalised,
        whitespace collapsed. So a mention whose excerpt contains these
        words, compared the same way, in a segment that starts inside the
        span, is the passage in today's transcript; its segment's resolved
        speaking row is the speaker. One row per distinct (speaker, segment).

        The comparison runs here rather than in SQL: collapsing whitespace
        the same way on both backends is simpler in Python, and a span
        holds a handful of mentions.
        """
        p = self.placeholder
        rows = self._fetch(
            f"""
            SELECT sp.entity_id AS speaker_entity_id, m.segment_id AS segment_id, m.quote_excerpt AS excerpt
            FROM entity_mentions m
            JOIN entity_mentions sp
              ON sp.episode_id = m.episode_id AND sp.segment_id = m.segment_id
             AND sp.role = 'speaking' AND sp.resolution_status = 'resolved' AND sp.entity_id IS NOT NULL
            WHERE m.episode_id = {p}
              AND (m.role IS NULL OR m.role <> 'speaking')
              AND m.start_ms >= {p} AND m.start_ms <= {p}
            """,
            [episode_id, start_ms, end_ms],
        )
        wanted = _comparable(text)
        found: Dict[tuple, Dict[str, Any]] = {}
        for row in rows:
            if wanted and wanted in _comparable(row.get("excerpt") or ""):
                key = (str(row["speaker_entity_id"]), int(row["segment_id"]))
                found[key] = {"speaker_entity_id": key[0], "segment_id": key[1]}
        return list(found.values())

    # ----- writes -----

    def set_speaker(self, share_id: str, speaker_entity_id: Optional[str], segment_id: int) -> None:
        """Rewrite a share's cached speaker and segment."""
        p = self.placeholder
        self._execute(
            f"UPDATE quote_shares SET speaker_entity_id = {p}, segment_id = {p} WHERE id = {p}",
            [speaker_entity_id, segment_id, share_id],
        )

    def revoke(self, share_id: str, reason: Optional[str], *, now: Optional[datetime] = None) -> bool:
        """Revoke one share. False when it does not exist or already is."""
        p = self.placeholder
        return (
            self._execute(
                f"UPDATE quote_shares SET revoked_at = {p}, revoked_reason = {p} "
                f"WHERE id = {p} AND revoked_at IS NULL",
                [self._timestamp(now or datetime.now(timezone.utc)), reason, share_id],
            )
            > 0
        )

    def revoke_for_podcast(
        self, podcast_id: str, reason: Optional[str], *, now: Optional[datetime] = None
    ) -> List[str]:
        """Revoke every live share of a show; returns their ids."""
        p = self.placeholder
        ids = [
            row["id"]
            for row in self._fetch(
                f"SELECT id FROM quote_shares WHERE podcast_id = {p} AND revoked_at IS NULL", [podcast_id]
            )
        ]
        if ids:
            self._execute(
                f"UPDATE quote_shares SET revoked_at = {p}, revoked_reason = {p} "
                f"WHERE podcast_id = {p} AND revoked_at IS NULL",
                [self._timestamp(now or datetime.now(timezone.utc)), reason, podcast_id],
            )
        return ids

    def record_hit(self, share_id: str, day: date, referrer_host: str) -> None:
        """Count one open of a short link on UTC ``day`` from ``referrer_host`` (``''`` for none)."""
        p = self.placeholder
        self._execute(
            f"""
            INSERT INTO quote_share_hits (share_id, day, referrer_host, hits) VALUES ({p}, {p}, {p}, 1)
            ON CONFLICT (share_id, day, referrer_host) DO UPDATE SET hits = quote_share_hits.hits + 1
            """,
            [share_id, self._day(day), referrer_host],
        )
