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

import json
import threading
import time
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Collection, Dict, Hashable, Iterator, Optional, Set, Tuple

from fastapi import HTTPException, Request, Response, status

from ..services.abuse.errors import AccountDisabledError
from .dependencies import _get_token_from_request
from .middleware.rate_limit import PUBLIC_LIMIT, allow_public_request

if TYPE_CHECKING:
    from ..models.podcast import Episode
    from .dependencies import AppState

# Hidden sets change only when an operator runs a takedown command (often
# from another process); a minute of staleness is the propagation delay.
# Every cached public payload is keyed on :meth:`PublicVisibility.fingerprint`,
# so a takedown reaches all of them on the same refresh.
_TTL_SECONDS = 60.0

PUBLIC_CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=300"
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


_ALL_CACHES: "weakref.WeakSet[TtlCache]" = weakref.WeakSet()

_CACHE_ONLY: ContextVar[bool] = ContextVar("public_cache_only", default=False)


class CacheMiss(Exception):
    """A :class:`TtlCache` lookup missed inside :func:`cache_only`."""


@contextmanager
def cache_only() -> Iterator[None]:
    """Within this block a cache miss raises :class:`CacheMiss` instead of
    loading. The page shell (spec #105 §4) builds from cached payloads
    first, and only an uncached build counts against its concurrency cap."""
    token = _CACHE_ONLY.set(True)
    try:
        yield
    finally:
        _CACHE_ONLY.reset(token)


class TtlCache:
    """Values computed at most once per TTL per key, bounded and thread-safe.

    Keys of public payloads include the visibility fingerprint, so a hidden
    show or entity drops out of every cached payload when the hidden sets
    next refresh, not when the payload's own TTL runs out.
    """

    def __init__(self, ttl: float, max_size: int = 512) -> None:
        self._ttl = ttl
        self._max_size = max_size
        self._lock = threading.Lock()
        self._entries: Dict[Hashable, Tuple[float, Any]] = {}
        _ALL_CACHES.add(self)

    def get_or_compute(self, key: Hashable, loader: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._entries.get(key)
            if hit and time.monotonic() - hit[0] <= self._ttl:
                return hit[1]
        if _CACHE_ONLY.get():
            raise CacheMiss()
        value = loader()
        with self._lock:
            if len(self._entries) >= self._max_size:
                self._entries.clear()
            self._entries[key] = (time.monotonic(), value)
        return value

    def lookup(self, key: Hashable) -> Tuple[bool, Any]:
        """``(True, value)`` for a fresh entry, else ``(False, None)``."""
        with self._lock:
            hit = self._entries.get(key)
            if hit and time.monotonic() - hit[0] <= self._ttl:
                return True, hit[1]
            return False, None

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            if len(self._entries) >= self._max_size:
                self._entries.clear()
            self._entries[key] = (time.monotonic(), value)

    def peek(self, key: Hashable) -> bool:
        """True when ``key`` has a fresh value (no load, no side effects)."""
        with self._lock:
            hit = self._entries.get(key)
            return bool(hit and time.monotonic() - hit[0] <= self._ttl)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def clear_public_caches() -> None:
    """Drop every public payload cache (tests, and in-process takedowns)."""
    for cache in list(_ALL_CACHES):
        cache.clear()


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

    def fingerprint(self) -> int:
        """Changes whenever the hidden show or entity sets change; part of
        every public payload cache key."""
        return hash((frozenset(self._hidden_podcasts.get().items()), frozenset(self._hidden_entities.get())))

    def invalidate(self) -> None:
        """Drop every cached answer (tests and takedown commands in-process)."""
        self._hidden_podcasts.clear()
        self._hidden_entities.clear()
        with self._lock:
            self._podcast_public.clear()
        clear_public_caches()


# ---------------------------------------------------------------------------
# Spec #105 decision 3 — which entity pages are indexed
# ---------------------------------------------------------------------------

#: The entity speaks: this many quotes said (or a guest or host role).
INDEX_MIN_SAID = 5
#: It is discussed in depth: this many quotes about it.
INDEX_MIN_ABOUT = 10
#: It is discussed widely: this many public episodes and quotes in total.
INDEX_WIDE_EPISODES = 2
INDEX_WIDE_QUOTES = 5
HUMAN_QID = "Q5"


@dataclass(frozen=True)
class EntityIndexFacts:
    """What decides whether an entity page is indexed (``index`` robots meta
    and a sitemap entry). The page builds this from its payload and the
    sitemap from one SQL pass; :func:`entity_is_indexable` decides both."""

    entity_id: str
    entity_type: str
    said: int
    about: int
    episodes: int
    has_role: bool
    wikidata_qid: Optional[str] = None
    instance_of: Tuple[str, ...] = ()


def instance_of_list(value: Any) -> Tuple[str, ...]:
    """``wikidata_instance_of`` as stored: a list (Postgres jsonb) or JSON
    text (SQLite)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    return tuple(str(v) for v in (value or ()) if v)


def entity_is_indexable(facts: EntityIndexFacts, noindex: Collection[str] = ()) -> bool:
    """The value test and the type test of spec #105 decision 3.

    Value (any one): the entity speaks (``INDEX_MIN_SAID`` quotes said, or a
    guest or host role); it is discussed in depth (``INDEX_MIN_ABOUT``
    quotes about it); or widely (``INDEX_WIDE_EPISODES`` episodes and
    ``INDEX_WIDE_QUOTES`` quotes in total). Type: a person whose Wikidata
    item is known not to be a human (Q5) fails, and so does a thing that
    is one. ``noindex`` is the operator's list for what the rule misses.
    """
    if facts.entity_id in noindex:
        return False
    if facts.wikidata_qid and facts.instance_of:
        human = HUMAN_QID in facts.instance_of
        if (facts.entity_type == "person") != human:
            return False
    speaks = facts.said >= INDEX_MIN_SAID or facts.has_role
    in_depth = facts.about >= INDEX_MIN_ABOUT
    widely = facts.episodes >= INDEX_WIDE_EPISODES and facts.said + facts.about >= INDEX_WIDE_QUOTES
    return speaks or in_depth or widely


def entity_noindex_ids(state: "AppState") -> Set[str]:
    """The operator's ``entity_noindex`` list (``thestill settings``)."""
    settings = getattr(getattr(state, "abuse", None), "settings", None)
    if settings is None:
        return set()
    try:
        return set(settings.get("entity_noindex"))
    except Exception:  # noqa: BLE001 - an unreadable list indexes by the rule alone
        return set()


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

    Anonymous: shareable for 5 minutes, served stale for 5 more while
    revalidating (a takedown is visible everywhere within ~11 minutes). Any credential: private. ``Vary`` always, so a shared
    cache never crosses the two.
    """
    response.headers["Cache-Control"] = PRIVATE_CACHE_CONTROL if has_credential(request) else PUBLIC_CACHE_CONTROL
    response.headers["Vary"] = "Cookie, Authorization"


def has_valid_session(request: Request) -> bool:
    """True when the request carries a credential that resolves to a user."""
    token = _get_token_from_request(request)
    if not token:
        return False
    try:
        return request.app.state.app_state.auth_service.get_current_user(token) is not None
    except AccountDisabledError:
        return False  # spec #104: a disabled account reads as signed out


def public_rate_limit(request: Request) -> None:
    """Router dependency: charge anonymous callers the public bucket.

    A request with a *valid* session skips it (signed-in users are limited
    elsewhere); an invalid or missing credential pays, so a junk cookie is
    no way around the limit.
    """
    if has_valid_session(request):
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
