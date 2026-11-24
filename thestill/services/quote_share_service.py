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
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from structlog import get_logger
from urllib3.util.retry import Retry

from ..core.quote_cards import TEMPLATE_VERSION, QuoteCard, can_render, render_og
from ..models.entities import display_name
from ..repositories.quote_share_repository import QuoteShareRepository
from ..utils.url_guard import guarded_session, validate_public_url

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
_ARTWORK_CONNECT_SECONDS = 3
_ARTWORK_READ_SECONDS = 4
#: The whole artwork download, however slowly the bytes arrive.
_ARTWORK_TOTAL_SECONDS = 8
#: At most this many cards render at once, process-wide (spec §8).
_render_slots = threading.BoundedSemaphore(2)
#: At most this many artwork downloads exist at once, abandoned ones
#: included: a download can outlive its render slot until its socket lets
#: go, and must not pile up behind a host that never does. Over the budget
#: a card renders without artwork.
_artwork_slots = threading.BoundedSemaphore(4)

_DANGLING_OPENERS = re.compile(
    r"^(he|she|it|they|them|him|her|his|their|this|that|these|those|and|but|so|or|because|which|who|like|yeah|well)\b",
    re.I,
)


def stands_alone(text: str) -> bool:
    """A quote that reads on its own: it opens a sentence (capital, no
    pronoun or connective) and ends one."""
    text = text.strip().strip('“”"')
    return bool(text) and text[0].isupper() and not _DANGLING_OPENERS.match(text) and text[-1] in ".!?"


#: Words a spoken sentence often opens with that a written quote drops:
#: "And when I talked to him..." is quoted as "When I talked to him...".
#: Only where the word cannot be part of the sentence:
#: - "And", "But", "Um" and "Uh" always;
#: - "So" unless it starts a phrase ("So far", "So much", "So that");
#: - "Well", "Yeah", "Yes", "Like", "Oh", "Okay", "Right", "Look", "You know"
#:   and "I mean" only with their comma ("Like it or not", "Well done" and
#:   "You know what..." stay).
_LEADING_FILLER = re.compile(
    r"^(?:"
    r"(?:and|but|um|uh)\b,?\s+"
    r"|so\b(?:,\s*|\s+(?!(?:far|much|many|long|few|little|too|that|called|what|on)\b))"
    r"|(?:well|yeah|yes|like|oh|okay|ok|right|look|listen|you know|i mean)\s*,\s*"
    r")+",
    re.I,
)
#: A sentence-end candidate: the mark, any closing quote or bracket after
#: it, then a space or the end. ``_sentence_ends`` drops abbreviations.
_SENTENCE_END = re.compile(r"[.!?][\"”’')\]]*(?=\s|$)")
#: Words whose full stop does not end a sentence ("Dr. Smith", "the U.S. market").
_ABBREVIATIONS = frozenset(
    "mr mrs ms dr prof st sr jr vs etc inc ltd co corp no vol approx dept est fig gen gov sen rep rev".split()
)
_INITIALS = re.compile(r"^(?:[A-Za-z]\.)*[A-Za-z]$")


def _sentence_ends(text: str) -> List[int]:
    """Where sentences end in ``text`` (offsets just past the mark).

    A full stop after an abbreviation or initials ("Dr.", "U.S.", "e.g.")
    is not an end, unless the text ends there. Nor is the end of a quoted
    sentence inside a longer one: a closing mark followed by a lower-case
    word ('The question "How?" is one that...').
    """
    ends: List[int] = []
    for match in _SENTENCE_END.finditer(text):
        closes_quotation = any(mark in match.group(0) for mark in "\"”’'")
        rest = text[match.end() :].lstrip()
        if closes_quotation and rest and rest[0].islower():
            continue
        # At the very end of the text an abbreviation's stop also ends the
        # sentence: "...their data centres in the U.S."
        if text[match.start()] == "." and match.end() < len(text.rstrip()):
            word = re.search(r"([A-Za-z.]+)$", text[: match.start()])
            token = word.group(1) if word else ""
            if token.lower().rstrip(".") in _ABBREVIATIONS or _INITIALS.match(token):
                continue
        ends.append(match.end())
    return ends


def _first_letter(text: str) -> int:
    """Where the text's first word starts, past any opening quotation mark."""
    index = 0
    while index < len(text) - 1 and text[index] in "“\"‘'(":
        index += 1
    return index


def _strip_enclosing_quotes(text: str) -> str:
    """Drop quotation marks that enclose the whole text, and only those:
    the opening mark must be closed by the very last one. Separate
    quotations at the start and the end ("A." Then "B.") stay as they are."""
    if len(text) < 2:
        return text
    if text.startswith("“") and text.endswith("”"):
        depth = 0
        for i, ch in enumerate(text):
            depth += 1 if ch == "“" else -1 if ch == "”" else 0
            if depth == 0:
                return text[1:-1].strip() if i == len(text) - 1 else text
    if text.startswith('"') and text.endswith('"') and text.find('"', 1) == len(text) - 1:
        return text[1:-1].strip()
    return text


#: A card's quote is at least this long after trimming (the gate's floor).
CARD_MIN_CHARS = 60


def _quotes_balanced(text: str) -> bool:
    """Every quotation the text opens, it also closes: double marks, and
    single curly marks, where a closing ’ inside a word ("it’s") is an
    apostrophe, not a close."""
    single_closes = len(re.findall(r"’(?![A-Za-z])", text))
    return text.count('"') % 2 == 0 and text.count("“") == text.count("”") and text.count("‘") <= single_closes


def card_text(text: str) -> Optional[str]:
    """The words a quote's card shows, or None when no card can be made.

    A quote is cut from speech, so it often opens with "And", "So" or
    "Well", starts mid-sentence, or stops mid-sentence. The card keeps the
    whole sentences inside it, the way a written quote would:

    - a leading filler word is dropped and the next word capitalised;
    - a start mid-sentence (lower case) moves on to the next sentence;
    - an end mid-sentence moves back to the last full stop;
    - a quote longer than ``CARD_MAX_CHARS`` keeps its sentences that fit.

    What is left must be at least ``CARD_MIN_CHARS`` long, open with a
    capital and end a sentence, and the card's fonts must have its letters.
    A quote that opens with a pronoun ("They...", "This...") is kept: the
    card names the speaker and the show. Never rewords anything else.
    """
    text = _strip_enclosing_quotes(" ".join(text.split()))
    if text and text[_first_letter(text)].islower():
        # Cut mid-sentence: start at the next sentence, if there is one.
        # (Judged before any filler word is dropped: "So when..." is a
        # whole sentence, "when..." alone is not.)
        first_ends = _sentence_ends(text)
        text = text[first_ends[0] :].strip() if first_ends else ""
    text = _LEADING_FILLER.sub("", text)
    if not text:
        return None
    first = _first_letter(text)
    text = text[:first] + text[first].upper() + text[first + 1 :]
    # Keep whole sentences, up to the length the card takes, and never stop
    # inside a quotation the speaker opened.
    ends = [end for end in _sentence_ends(text) if end <= CARD_MAX_CHARS and _quotes_balanced(text[:end])]
    if not ends:
        return None
    text = text[: ends[-1]].strip()
    if len(text) < CARD_MIN_CHARS or not text[_first_letter(text)].isupper() or not can_render(text):
        return None
    return text


def card_eligible(text: str) -> bool:
    """Whether a public quote can become a card (``card_text`` finds one)."""
    return card_text(text) is not None


def share_id(episode_id: str, start_ms: int, text: str) -> str:
    """The id of a gated quote: base32 of the first 50 bits of its hash."""
    digest = hashlib.sha256(f"{episode_id}|{int(start_ms)}|{text}".encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii").lower()[:10]


def _shutdown_socket(response: Any) -> None:
    """End a download blocked in a read by shutting its socket down."""
    try:
        sock = getattr(getattr(getattr(response, "raw", None), "connection", None), "sock", None)
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except Exception:  # already closed, or never connected
        pass


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
            text = card_text(row.get("quote") or "")
            if text is None or not row.get("podcast_id"):
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
        # Rendering fetches artwork and writes a file: paused by its own
        # switch and by read-only mode. Cached cards above still serve.
        if not self.rendering_enabled or self._setting("read_only", False):
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
        """The share's artwork, or None. Never takes longer than
        ``_ARTWORK_TOTAL_SECONDS``, whatever the other server does: this
        runs holding one of the two render slots.

        The download runs on its own thread. A socket timeout bounds only
        the wait for the *next* bytes, so a server that trickles them could
        otherwise hold the slot for as long as it liked; at the deadline the
        caller stops waiting and returns. The abandoned download is closed
        from another thread and ends by itself at its next read timeout.
        """
        if not share.image_url:
            return None
        url = share.image_url
        state: Dict[str, Any] = {}
        if not _artwork_slots.acquire(blocking=False):
            logger.info("quote_card_artwork_unavailable", share_id=share.id, error="download_budget")
            return None

        def download() -> None:
            try:
                _download()
            finally:
                _artwork_slots.release()

        def _download() -> None:
            try:
                validate_public_url(url)
                # One attempt, no retries: the default session sleeps
                # through an upstream's Retry-After.
                with guarded_session(retries=Retry(total=0), user_agent="Thestill/1.0") as session:
                    response = session.get(url, timeout=(_ARTWORK_CONNECT_SECONDS, _ARTWORK_READ_SECONDS), stream=True)
                    state["response"] = response
                    response.raise_for_status()
                    body = b""
                    while not state.get("abandoned"):
                        # Decoded: a host may gzip the image on the wire.
                        chunk = response.raw.read1(64 * 1024, decode_content=True)
                        if not chunk:
                            state["body"] = body
                            return
                        body += chunk
                        if len(body) > _ARTWORK_MAX_BYTES:
                            return
            except Exception as exc:
                state["error"] = type(exc).__name__

        worker = threading.Thread(target=download, name="quote-card-artwork", daemon=True)
        worker.start()
        worker.join(_ARTWORK_TOTAL_SECONDS)
        if worker.is_alive():
            state["abandoned"] = True
            # Shut the socket itself: that needs no lock and ends the blocked
            # read, where closing the response can wait for that read. The
            # caller, and its render slot, are released now either way.
            _shutdown_socket(state.get("response"))
            state["error"] = "deadline"
            logger.info("quote_card_artwork_unavailable", share_id=share.id, error="deadline")
            return None
        body = state.get("body")
        if body is None:
            logger.info("quote_card_artwork_unavailable", share_id=share.id, error=state.get("error", "too_large"))
            return None
        return body

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

    # ----- after a transcript or entity change -----

    def reconcile_episode(self, episode_id: str) -> Dict[str, int]:
        """Re-resolve the speaker of every share of an episode against its
        mentions as they are now (spec §7). Called when the entity refresh
        of an episode completes.

        The anchor is the share's words inside its source span. Exactly one
        speaker says them there: that is the speaker. None or several: the
        share loses its name rather than guess. A changed share has its
        cards deleted, so the next request renders the current name.
        """
        counts = {"checked": 0, "changed": 0, "unnamed": 0}
        changed: List[str] = []
        for share in self._repo.live_for_episode(episode_id):
            counts["checked"] += 1
            found = self._repo.speakers_of_passage(
                episode_id, share["text"], int(share["start_ms"]), int(share["end_ms"])
            )
            speakers = {str(row["speaker_entity_id"]) for row in found}
            if len(speakers) == 1:
                speaker: Optional[str] = next(iter(speakers))
                segment = min(int(row["segment_id"]) for row in found)
            else:
                speaker, segment = None, int(share["segment_id"])
                counts["unnamed"] += 1
            if speaker != share["speaker_entity_id"] or segment != int(share["segment_id"]):
                self._repo.set_speaker(share["id"], speaker, segment)
                if speaker != share["speaker_entity_id"]:
                    changed.append(share["id"])
        counts["changed"] = len(changed)
        self.delete_assets(changed)
        if counts["checked"]:
            logger.info("quote_shares_reconciled", episode_id=episode_id, **counts)
        return counts

    def forget_speaker_cards(self, entity_id: str) -> int:
        """Delete the cards of every share that names this entity (it was
        hidden, so its shares now answer 410). Returns how many shares."""
        ids = self._repo.ids_for_speaker(entity_id)
        self.delete_assets(ids)
        return len(ids)

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
