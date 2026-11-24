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

"""Quote shares (spec #114 Phase 0): short links to public quotes.

A share is one public quote with a stable id. ``/q/<id>`` previews as the
quote's card and leads to the episode page at that moment. This service
mints shares when a public quote payload is built, resolves an id to what
a card and its landing show, renders and caches the card, counts opens and
revokes.

The id is a truncated hash of the episode, the quote's start and its words,
so a quote always has the same link and an id cannot be reversed: resolving
is a lookup of ``quote_shares``, never a search of the quotes.
"""

import base64
import hashlib
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from structlog import get_logger

from ..core.quote_cards import TEMPLATE_VERSION, QuoteCard, render_og
from ..models.entities import display_name
from ..repositories.quote_share_repository import QuoteShareRepository
from ..utils.url_guard import UnsafeURLError, guarded_get

if TYPE_CHECKING:
    from ..services.abuse.actor import Actor
    from ..services.abuse.audit_service import AuditLog
    from ..services.abuse.settings_service import SettingsService
    from ..utils.config import Config

logger = get_logger(__name__)

#: The longest quote a card takes (spec §1); longer text does not fit the card.
CARD_MAX_CHARS = 320
SHARE_ID_RE = re.compile(r"^[a-z2-7]{10,12}$")
_ARTWORK_MAX_BYTES = 8 * 1024 * 1024
_ARTWORK_TIMEOUT_SECONDS = 6
#: At most this many cards render at once, process-wide (spec §8).
_render_slots = threading.BoundedSemaphore(2)

_DANGLING_OPENERS = re.compile(
    r"^(he|she|it|they|them|him|her|his|their|this|that|these|those|and|but|so|or|because|which|who|like|yeah|well)\b",
    re.I,
)


def stands_alone(text: str) -> bool:
    """A quote that reads on its own: it opens a sentence (capital, no
    pronoun or connective) and ends one."""
    text = text.strip().strip('“”"')
    return bool(text) and text[0].isupper() and not _DANGLING_OPENERS.match(text) and text[-1] in ".!?"


def card_eligible(text: str) -> bool:
    """Whether a public quote may become a card: it stands alone and fits."""
    return len(text.strip()) <= CARD_MAX_CHARS and stands_alone(text)


def share_id(episode_id: str, start_ms: int, text: str) -> str:
    """The id of a gated quote: base32 of the first 50 bits of its hash."""
    digest = hashlib.sha256(f"{episode_id}|{int(start_ms)}|{text}".encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii").lower()[:10]


class ShareGone(Exception):
    """The share existed and was withdrawn: revoked, or its speaker hidden."""


@dataclass(frozen=True)
class QuoteShare:
    """A resolved share: what the card, the ``/q`` page and the landing show."""

    id: str
    text: str
    start_ms: int
    #: None when the speaker can no longer be named.
    speaker_name: Optional[str]
    speaker_id: Optional[str]
    podcast_slug: str
    podcast_title: str
    episode_slug: str
    episode_title: str
    image_url: Optional[str]
    template_version: int

    @property
    def start_seconds(self) -> int:
        return self.start_ms // 1000

    @property
    def episode_path(self) -> str:
        return f"/podcasts/{self.podcast_slug}/episodes/{self.episode_slug}"

    @property
    def landing_path(self) -> str:
        """Where the short link leads: the episode at that moment, with the
        share id in the fragment so the page shows this quote."""
        return f"{self.episode_path}#t={self.start_seconds}&s={self.id}"


class QuoteShareService:
    def __init__(
        self,
        repository: QuoteShareRepository,
        config: "Config",
        *,
        settings: Optional["SettingsService"] = None,
        audit: Optional["AuditLog"] = None,
    ) -> None:
        self._repo = repository
        self._config = config
        self._settings = settings
        self._audit = audit

    # ----- switches -----

    def _setting(self, key: str, default: bool) -> bool:
        if self._settings is None:
            return default
        try:
            return bool(self._settings.get(key))
        except Exception:
            logger.exception("quote_share_setting_read_failed", key=key)
            return False if key == "quote_cards_enabled" else True

    @property
    def rendering_enabled(self) -> bool:
        return self._setting("quote_cards_enabled", True)

    # ----- minting -----

    def mint(self, rows: Iterable[Dict[str, Any]]) -> Dict[Any, str]:
        """Persist a share for every card-eligible quote row and return
        ``{mention_id: share_id}`` for the ones that may be shown.

        ``rows`` are public quote rows (``public_quotes._COLUMNS``). Never
        raises: a quote without a share keeps its plain link to the moment.
        Mints nothing in read-only mode.
        """
        candidates: Dict[str, Dict[str, Any]] = {}
        by_mention: Dict[Any, str] = {}
        for row in rows:
            text = (row.get("quote") or "").strip()
            if not card_eligible(text) or not row.get("podcast_id"):
                continue
            start_ms = int(row.get("start_ms") or 0)
            new_id = share_id(str(row["episode_id"]), start_ms, text)
            candidates[new_id] = {
                "id": new_id,
                "episode_id": str(row["episode_id"]),
                "podcast_id": str(row["podcast_id"]),
                "segment_id": int(row["segment_id"]),
                "start_ms": start_ms,
                "end_ms": int(row.get("turn_end_ms") or start_ms),
                "text": text,
                "speaker_entity_id": str(row["speaker_id"]) if row.get("speaker_id") else None,
            }
            by_mention[row["mention_id"]] = new_id
        if not candidates or self._setting("read_only", False):
            return {}
        try:
            usable = self._repo.upsert_gated(candidates.values())
        except Exception:
            logger.exception("quote_share_mint_failed", count=len(candidates))
            return {}
        return {mention: new_id for mention, new_id in by_mention.items() if usable.get(new_id)}

    # ----- resolving -----

    def resolve(self, share_id_: str) -> Optional[QuoteShare]:
        """The share for an id. None when there is none or it is not public;
        raises ``ShareGone`` when it was withdrawn."""
        if not SHARE_ID_RE.match(share_id_ or ""):
            return None
        row = self._repo.get(share_id_)
        if row is None:
            return None
        if row["revoked_at"] is not None or row["speaker_hidden"]:
            raise ShareGone(share_id_)
        if not row["podcast_public"] or not row["episode_public"] or not row["podcast_slug"] or not row["episode_slug"]:
            return None
        speaker = row.get("speaker_name")
        return QuoteShare(
            id=row["id"],
            text=row["text"],
            start_ms=int(row["start_ms"]),
            speaker_name=display_name(speaker) if speaker else None,
            speaker_id=str(row["speaker_id"]) if row.get("speaker_id") else None,
            podcast_slug=row["podcast_slug"],
            podcast_title=row["podcast_title"] or "",
            episode_slug=row["episode_slug"],
            episode_title=row["episode_title"] or "",
            image_url=row.get("episode_image_url") or row.get("podcast_image_url"),
            template_version=int(row.get("template_version") or TEMPLATE_VERSION),
        )

    def record_open(self, share: QuoteShare, referrer_host: str) -> None:
        """Count one open of the short link. Never raises."""
        try:
            self._repo.record_hit(share.id, datetime.now(timezone.utc).date(), referrer_host[:200])
        except Exception:
            logger.exception("quote_share_hit_failed", share_id=share.id)

    # ----- the card -----

    def short_url(self, share: QuoteShare) -> str:
        return f"{self._config.public_base_url}/q/{share.id}"

    def card_etag(self, share: QuoteShare) -> str:
        """Changes with the template and with the speaker's name, so a card
        rendered for another name is never taken for this one."""
        name = hashlib.sha256((share.speaker_name or "").encode("utf-8")).hexdigest()[:8]
        return f"v{TEMPLATE_VERSION}-{name}"

    def _card_key(self, share: QuoteShare) -> str:
        paths = self._config.path_manager
        return paths.to_relative(paths.quote_card_file(share.id, "og", self.card_etag(share)))

    def card_png(self, share: QuoteShare) -> Optional[bytes]:
        """The link-preview card, from the cache or rendered now. None when
        it is not cached and rendering is switched off."""
        storage = self._config.file_storage
        key = self._card_key(share)
        try:
            if storage.exists(key):
                return storage.read_bytes(key)
        except Exception:
            logger.exception("quote_card_read_failed", share_id=share.id)
        if not self.rendering_enabled:
            return None
        with _render_slots:
            shown = urlparse(self.short_url(share))
            png = render_og(
                QuoteCard(
                    quote=share.text,
                    speaker=share.speaker_name,
                    show=share.podcast_title,
                    episode_title=share.episode_title,
                    short_url=f"{shown.netloc}{shown.path}" if shown.netloc else f"/q/{share.id}",
                    artwork=self._artwork(share),
                )
            )
        try:
            storage.write_bytes(key, png)
        except Exception:
            logger.exception("quote_card_write_failed", share_id=share.id)
        return png

    def _artwork(self, share: QuoteShare) -> Optional[bytes]:
        if not share.image_url:
            return None
        try:
            response = guarded_get(share.image_url, timeout=_ARTWORK_TIMEOUT_SECONDS, stream=True)
            response.raise_for_status()
            body = response.raw.read(_ARTWORK_MAX_BYTES + 1, decode_content=True)
            return body if len(body) <= _ARTWORK_MAX_BYTES else None
        except (UnsafeURLError, Exception) as exc:
            logger.info("quote_card_artwork_unavailable", share_id=share.id, error=type(exc).__name__)
            return None

    def delete_assets(self, share_ids: Iterable[str]) -> None:
        """Delete every rendered card of these shares. Never raises."""
        storage = self._config.file_storage
        paths = self._config.path_manager
        for one in share_ids:
            try:
                prefix = paths.to_relative(paths.quote_cards_dir() / one)
                keys = [meta.path for meta in storage.list_files(prefix)]
                if keys:
                    storage.delete_batch(keys)
            except Exception:
                logger.exception("quote_card_delete_failed", share_id=one)

    # ----- revoking -----

    def revoke(self, share_id_: str, *, actor: "Actor", reason: Optional[str] = None) -> bool:
        """Withdraw one share: ``/q/<id>`` answers 410 and its cards are deleted."""
        done = self._repo.revoke(share_id_, reason)
        if done:
            self.delete_assets([share_id_])
            if self._audit is not None:
                self._audit.record(actor, "quote_share.revoke", share_id=share_id_, reason=reason)
        return done

    def revoke_for_podcast(self, podcast_id: str, *, reason: str) -> List[str]:
        """Withdraw every share of a show (it left the public pages)."""
        ids = self._repo.revoke_for_podcast(podcast_id, reason)
        self.delete_assets(ids)
        if ids:
            logger.info("quote_shares_revoked_for_podcast", podcast_id=podcast_id, count=len(ids))
        return ids

    def list_for_episode(self, episode_id: str) -> List[Dict[str, Any]]:
        return self._repo.list_for_episode(episode_id)
