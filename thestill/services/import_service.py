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
Import-arbitrary-episodes service (spec #31, Phase 1).

Lets a user paste an external URL and have the resulting episode land in
their inbox immediately. The pipeline (download → downsample → transcribe →
clean → summarize → entity branch) runs unchanged in the background; this
module only handles URL resolution, parent-podcast bootstrap, and inbox-row
creation.

Phase 1 scope: ``BareAudioResolver`` only — direct audio URLs (.mp3, .m4a,
.opus, .ogg, .wav). YouTube and Apple resolvers ship in later phases.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

from structlog import get_logger

from ..core.queue_manager import QueueManager, TaskStage
from ..models.inbox import InboxEntry
from ..repositories.inbox_repository import InboxRepository
from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository

logger = get_logger(__name__)


# Audio file extensions recognised by ``BareAudioResolver``. Lower-case;
# matches are case-insensitive.
_BARE_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".opus", ".ogg", ".wav")

# Tracking / analytics query params we drop during URL normalisation so the
# same logical episode shared with different campaign tags collapses to the
# same canonical id. Conservative list: only well-known tracking keys.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
    }
)


class ImportError(Exception):
    """Base exception for import failures."""


class UnsupportedUrlError(ImportError):
    """No registered resolver can handle this URL."""


class ResolverError(ImportError):
    """A resolver matched the URL but failed to build a CanonicalSource."""


@dataclass(frozen=True)
class CanonicalSource:
    """
    Normalised, resolver-issued description of an importable URL.

    Spec #31 also defines a ``parent`` field (CanonicalParent) for resolvers
    that can deduce the source podcast — that ships in Phase 2 alongside the
    YouTube + Apple resolvers. Phase 1 only carries the bare-audio case
    where there's no parent to deduce.
    """

    kind: str  # "bare_audio" | "youtube" | "rss_episode" (Phase 1: bare_audio only)
    canonical_id: str  # e.g. "audio:<sha256>"
    audio_url: str  # what the download stage fetches
    title: str
    description: str = ""
    duration_seconds: Optional[int] = None
    pub_date: Optional[datetime] = None
    image_url: Optional[str] = None
    source_handle: str = ""  # display label (host for bare audio)
    external_id: str = ""  # episode-level external id (sha256 for bare audio)


class Resolver(Protocol):
    """Pluggable URL → CanonicalSource adapter."""

    def matches(self, url: str) -> bool: ...

    def resolve(self, url: str) -> CanonicalSource: ...


def _normalise_url(url: str) -> str:
    """
    Stable form of a URL for canonical-id derivation.

    Lower-cases the host, strips tracking query params, drops the fragment.
    Path case is preserved — many CDNs treat the path as case-sensitive.
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    if parsed.username or parsed.password:
        userinfo = parsed.username or ""
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        netloc = f"{userinfo}@{host}" if userinfo else host
    else:
        netloc = host

    if parsed.query:
        kept = [
            (k, v)
            for k, v in (
                pair.partition("=")[::2] for pair in parsed.query.split("&") if pair
            )
            if k.lower() not in _TRACKING_PARAMS
        ]
        query = "&".join(f"{k}={v}" if v else k for k, v in kept)
    else:
        query = ""

    return urlunparse((parsed.scheme.lower(), netloc, parsed.path, parsed.params, query, ""))


class BareAudioResolver:
    """
    Resolver for direct audio URLs (no RSS feed, no platform metadata).

    Phase 1 stays offline: ``resolve`` does NOT make a network call. We
    derive everything we need from the URL itself — extension, filename,
    host. The actual audio is fetched later by the existing download stage,
    which is also where transport errors surface.
    """

    def matches(self, url: str) -> bool:
        try:
            parsed = urlparse(url.strip())
        except ValueError:
            return False
        if parsed.scheme not in ("http", "https"):
            return False
        path = (parsed.path or "").lower()
        return path.endswith(_BARE_AUDIO_EXTENSIONS)

    def resolve(self, url: str) -> CanonicalSource:
        normalised = _normalise_url(url)
        digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
        canonical_id = f"audio:{digest}"
        parsed = urlparse(normalised)
        filename = (parsed.path.rsplit("/", 1)[-1] or "audio").rsplit(".", 1)[0]
        title = filename.replace("_", " ").replace("-", " ").strip() or "Imported audio"
        return CanonicalSource(
            kind="bare_audio",
            canonical_id=canonical_id,
            audio_url=normalised,
            title=title,
            source_handle=parsed.hostname or "",
            external_id=digest,
        )


@dataclass(frozen=True)
class ImportResult:
    """Outcome of ``ImportService.import_url``."""

    episode_id: str
    canonical_id: str
    title: str
    kind: str
    source_handle: str
    inbox_entry: InboxEntry
    episode_created: bool
    inbox_created: bool


class ImportService:
    """
    Orchestrates URL → episode → inbox-row materialisation.

    Idempotency:

    - Re-importing the same URL by the same user returns the existing
      episode + the existing inbox row (no second pipeline task).
    - Re-importing the same URL by a *different* user adds a new inbox
      row pointing at the shared episode (no second Whisper run).
    """

    def __init__(
        self,
        repository: SqlitePodcastRepository,
        inbox_repository: InboxRepository,
        queue_manager: QueueManager,
        resolvers: Optional[Sequence[Resolver]] = None,
    ) -> None:
        self._repository = repository
        self._inbox_repo = inbox_repository
        self._queue = queue_manager
        # Default resolver lineup is intentionally minimal — Phase 1.
        self._resolvers: List[Resolver] = list(resolvers) if resolvers else [BareAudioResolver()]
        logger.info("ImportService initialized", resolvers=[type(r).__name__ for r in self._resolvers])

    def import_url(self, *, user_id: str, url: str) -> ImportResult:
        """Import ``url`` into ``user_id``'s inbox. See class docstring for idempotency."""
        canonical = self._resolve(url)
        episode_id, episode_created = self._find_or_create_episode(canonical)
        inbox_entry, inbox_created = self._inbox_repo.find_or_create(
            user_id=user_id,
            episode_id=episode_id,
            source="import",
        )
        if episode_created:
            self._queue.add_task(
                episode_id=episode_id,
                stage=TaskStage.DOWNLOAD,
                metadata={"run_full_pipeline": True, "initiated_by": "import"},
            )
            logger.info(
                "import_pipeline_enqueued",
                episode_id=episode_id,
                canonical_id=canonical.canonical_id,
                kind=canonical.kind,
            )
        logger.info(
            "import_completed",
            episode_id=episode_id,
            canonical_id=canonical.canonical_id,
            kind=canonical.kind,
            episode_created=episode_created,
            inbox_created=inbox_created,
            user_id=user_id,
        )
        return ImportResult(
            episode_id=episode_id,
            canonical_id=canonical.canonical_id,
            title=canonical.title,
            kind=canonical.kind,
            source_handle=canonical.source_handle,
            inbox_entry=inbox_entry,
            episode_created=episode_created,
            inbox_created=inbox_created,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve(self, url: str) -> CanonicalSource:
        for resolver in self._resolvers:
            if resolver.matches(url):
                try:
                    return resolver.resolve(url)
                except ImportError:
                    raise
                except Exception as exc:
                    raise ResolverError(
                        f"{type(resolver).__name__} failed to resolve {url!r}: {exc}"
                    ) from exc
        raise UnsupportedUrlError(
            f"No resolver matched URL {url!r}. v1 supports direct audio links "
            "(.mp3, .m4a, .opus, .ogg, .wav)."
        )

    def _find_or_create_episode(self, canonical: CanonicalSource) -> Tuple[str, bool]:
        """Return ``(episode_id, created)`` for ``canonical``."""
        existing_id = self._repository.find_episode_id_by_canonical_id(canonical.canonical_id)
        if existing_id is not None:
            return existing_id, False

        # Phase 1: every resolver's parent is the synthetic audio-imports
        # row. Phase 2 will branch here on canonical.parent.
        parent_id = self._repository.ensure_synthetic_audio_imports_parent()
        episode_id = self._repository.insert_imported_episode(
            podcast_id=parent_id,
            canonical_id=canonical.canonical_id,
            external_id=canonical.external_id or canonical.canonical_id,
            title=canonical.title,
            audio_url=canonical.audio_url,
            description=canonical.description,
            pub_date=canonical.pub_date,
            duration=canonical.duration_seconds,
            image_url=canonical.image_url,
        )
        return episode_id, True
