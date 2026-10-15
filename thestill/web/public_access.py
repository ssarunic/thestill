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

"""Who may see what on signed-out pages (spec #94).

One place answers "is this public?" so the public API, the page shell and
the sitemap never disagree (FM-6):

- a podcast is public when ``public_pages`` is on and it is not a synthetic
  import container (:meth:`PodcastRepository.is_podcast_public`);
- an episode is public when its podcast is and it has a real summary
  (the same predicate as :meth:`EpisodeRepository.list_public_episode_rows`);
- an entity is public unless ``public_hidden`` is on.

Hidden sets are tiny (takedowns), so they are cached in-process for a short
TTL and filtered in memory. Also here: the cache-header policy for public
responses and the rate-limit dependency for anonymous callers.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Callable, Dict, Optional, Set, Tuple

from fastapi import HTTPException, Request, Response, status

from .dependencies import _get_token_from_request
from .middleware.rate_limit import PUBLIC_LIMIT, allow_public_request

if TYPE_CHECKING:
    from ..models.podcast import Episode
    from .dependencies import AppState

# Hidden sets change only when an operator runs a takedown command; a
# minute of staleness is the documented propagation delay.
_TTL_SECONDS = 60.0

PUBLIC_CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=3600"
PRIVATE_CACHE_CONTROL = "private, no-cache"


class _TtlValue:
    """A value recomputed at most once per TTL, safe across threads."""

    def __init__(self, loader: Callable[[], object], ttl: float = _TTL_SECONDS) -> None:
        self._loader = loader
        self._ttl = ttl
        self._lock = threading.Lock()
        self._value: object = None
        self._loaded_at = 0.0

    def get(self):
        now = time.monotonic()
        with self._lock:
            if self._value is None or now - self._loaded_at > self._ttl:
                self._value = self._loader()
                self._loaded_at = now
            return self._value

    def clear(self) -> None:
        with self._lock:
            self._value = None


class PublicVisibility:
    """The spec #94 visibility predicate, shared by every signed-out surface."""

    def __init__(self, podcast_repository, entity_repository) -> None:
        self._podcasts = podcast_repository
        self._entities = entity_repository
        self._hidden_podcasts = _TtlValue(lambda: dict(podcast_repository.list_hidden_podcasts()))
        self._hidden_entities = _TtlValue(lambda: set(entity_repository.list_hidden_entity_ids()))
        self._podcast_public: Dict[str, Tuple[bool, float]] = {}
        self._lock = threading.Lock()

    # -- podcasts -------------------------------------------------------

    def hidden_podcast_ids(self) -> Set[str]:
        return set(self._hidden_podcasts.get().keys())

    def hidden_podcast_slugs(self) -> Set[str]:
        return set(self._hidden_podcasts.get().values())

    def podcast_is_public(self, podcast_id: str) -> bool:
        podcast_id = str(podcast_id)
        now = time.monotonic()
        with self._lock:
            cached = self._podcast_public.get(podcast_id)
            if cached and now - cached[1] <= _TTL_SECONDS:
                return cached[0]
        public = bool(self._podcasts.is_podcast_public(podcast_id))
        with self._lock:
            if len(self._podcast_public) > 4096:
                self._podcast_public.clear()
            self._podcast_public[podcast_id] = (public, now)
        return public

    # -- episodes -------------------------------------------------------

    @staticmethod
    def has_real_summary(episode: "Episode") -> bool:
        """Same rule as ``list_public_episode_rows``: a stored, non-empty
        preview. ``N/A`` summaries have no gist section, so no preview."""
        return bool(episode.summary_path) and bool((episode.summary_preview or "").strip())

    def episode_is_public(self, podcast_id: str, episode: "Episode") -> bool:
        return bool(episode.slug) and self.has_real_summary(episode) and self.podcast_is_public(podcast_id)

    # -- entities -------------------------------------------------------

    def hidden_entity_ids(self) -> Set[str]:
        return set(self._hidden_entities.get())

    def entity_is_public(self, entity_id: str) -> bool:
        return entity_id not in self._hidden_entities.get()

    def invalidate(self) -> None:
        """Drop every cached answer (tests and takedown commands in-process)."""
        self._hidden_podcasts.clear()
        self._hidden_entities.clear()
        with self._lock:
            self._podcast_public.clear()


_VISIBILITY_ATTR = "_public_visibility"


def get_public_visibility(state: "AppState") -> PublicVisibility:
    """The process-wide :class:`PublicVisibility` for ``state`` (lazily built)."""
    visibility = getattr(state, _VISIBILITY_ATTR, None)
    if visibility is None:
        visibility = PublicVisibility(state.repository, state.entity_repository)
        setattr(state, _VISIBILITY_ATTR, visibility)
    return visibility


def has_credential(request: Request) -> bool:
    """True when the request carries a session cookie or a bearer token.

    The cache policy keys on "a credential is present", not on whether it
    is valid: a response computed for a request that *might* be signed in
    must never be stored by a shared cache.
    """
    return _get_token_from_request(request) is not None


def apply_public_cache_headers(request: Request, response: Response) -> None:
    """Spec #94 §2 cache policy for every public API response.

    Anonymous: shareable for 5 minutes, served stale for an hour while
    revalidating. Any credential: private. ``Vary`` always, so a shared
    cache never crosses the two.
    """
    response.headers["Cache-Control"] = PRIVATE_CACHE_CONTROL if has_credential(request) else PUBLIC_CACHE_CONTROL
    response.headers["Vary"] = "Cookie, Authorization"


def public_rate_limit(request: Request) -> None:
    """Router dependency: charge anonymous callers the public bucket.

    A request with a *valid* session skips it (signed-in users are limited
    elsewhere); an invalid or missing credential pays, so a junk cookie is
    no way around the limit.
    """
    token = _get_token_from_request(request)
    if token:
        state = request.app.state.app_state
        if state.auth_service.get_current_user(token) is not None:
            return
    if not allow_public_request(request):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests from your network. Try again in a minute.",
            headers={
                "Retry-After": str(PUBLIC_LIMIT.window_seconds),
                "Cache-Control": PRIVATE_CACHE_CONTROL,
                "Vary": "Cookie, Authorization",
            },
        )


def public_base_url(state: "AppState") -> Optional[str]:
    """Canonical origin for absolute public URLs, or None when unset."""
    return state.config.public_base_url or None
