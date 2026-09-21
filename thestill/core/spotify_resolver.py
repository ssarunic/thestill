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
Spotify link resolution (spec #79).

Spotify episode and show IDs are opaque — they share nothing with Apple's
IDs or with the RSS GUID — so a pasted ``open.spotify.com`` link cannot be
mapped deterministically. What *can* be done reliably is a metadata-matching
pipeline:

1. **Read Spotify's own metadata** for the link. The public episode / show
   page exposes Open Graph tags (title, show name, release date, duration,
   artwork) with no auth. When ``SPOTIFY_CLIENT_ID`` / ``SPOTIFY_CLIENT_SECRET``
   are configured the Web API is used instead (it also carries the
   publisher, which sharpens the show match, and it does not depend on
   Spotify's page layout).
2. **Find the show in the Apple Podcasts directory** with the iTunes Search
   API, fuzzy-matching the show name (and publisher when known). That gives
   the show's RSS ``feedUrl`` — the platform-independent key Thestill
   already uses for every podcast.
3. **Find the episode** among the show's episodes — the iTunes 200-episode
   window first, an iTunes episode search next, and finally the RSS feed
   itself (full history). Candidates are scored on a normalised token-set
   title similarity, release-date proximity (±36 h absorbs timezone and
   publish-lag differences) and duration (a tiebreaker only — dynamic ad
   insertion makes Spotify and Apple durations differ).

A combined score below the acceptance threshold — typical for Spotify
exclusives and video-only shows, which have no public feed at all — is a
clean "not found" rather than a low-confidence guess.

Everything network-facing is injectable so the unit tests run offline.
"""

from __future__ import annotations

import base64
import html
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple
from urllib.parse import quote_plus

import feedparser
import requests
from structlog import get_logger
from urllib3.util.retry import Retry

from ..utils.datetime_utils import parse_struct_time_utc
from ..utils.duration import parse_duration
from ..utils.url_guard import guarded_session
from ..utils.url_patterns import extract_spotify_entity, is_spotify_short_link

logger = get_logger(__name__)


class SpotifyResolutionError(Exception):
    """A Spotify link was recognised but could not be mapped to a feed / episode.

    The message is user-facing: the import API surfaces it verbatim.
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

MetadataSource = Literal["page", "api"]


@dataclass(frozen=True)
class SpotifyEpisodeMetadata:
    """What Spotify tells us about an episode link."""

    episode_id: str
    title: str
    show_name: str
    publisher: Optional[str] = None
    description: str = ""
    release_date: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    image_url: Optional[str] = None
    source: MetadataSource = "page"


@dataclass(frozen=True)
class SpotifyShowMetadata:
    """What Spotify tells us about a show link."""

    show_id: str
    name: str
    publisher: Optional[str] = None
    description: str = ""
    image_url: Optional[str] = None
    source: MetadataSource = "page"


@dataclass(frozen=True)
class AppleShowMatch:
    """The Apple Podcasts directory entry chosen for a Spotify show."""

    collection_id: str
    name: str
    feed_url: str
    publisher: str = ""
    image_url: Optional[str] = None
    score: float = 0.0


@dataclass(frozen=True)
class EpisodeCandidate:
    """A show episode from any source (iTunes window, iTunes search, RSS)."""

    title: str
    audio_url: str
    pub_date: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    external_id: str = ""
    description: str = ""
    image_url: Optional[str] = None
    origin: str = ""


@dataclass(frozen=True)
class EpisodeScore:
    """Per-signal breakdown so log lines and tests can explain a decision."""

    title: float
    date: float
    duration: float
    combined: float
    accepted: bool


@dataclass(frozen=True)
class ResolvedSpotifyEpisode:
    """Outcome of :func:`SpotifyLinkResolver.resolve_episode`."""

    episode_id: str
    spotify: SpotifyEpisodeMetadata
    show: AppleShowMatch
    episode: EpisodeCandidate
    score: EpisodeScore


# ---------------------------------------------------------------------------
# HTTP plumbing (SSRF-guarded, browser UA — iTunes 403s non-browser agents)
# ---------------------------------------------------------------------------

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
# Spotify does the opposite: a browser UA gets the JavaScript web-player shell
# ("Spotify – Web Player", no Open Graph tags); the server-rendered page with
# the metadata is only served to non-browser agents (link unfurlers, bots).
_SPOTIFY_PAGE_USER_AGENT = "Thestill/1.0"
_SPOTIFY_SHELL_TITLE = "spotify web player"
_HTTP_TIMEOUT = 15
# Open Graph tags live in <head>; bounding the scanned prefix keeps the
# meta-tag regexes cheap on Spotify's multi-hundred-KB pages.
_MAX_PAGE_SCAN_BYTES = 512 * 1024

_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SPOTIFY_API_BASE = "https://api.spotify.com/v1"


def _retry_policy() -> Retry:
    return Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )


def _guarded_get(
    url: str,
    *,
    label: str,
    headers: Optional[Dict[str, str]] = None,
    user_agent: str = _BROWSER_USER_AGENT,
) -> requests.Response:
    try:
        with guarded_session(user_agent=user_agent, retries=_retry_policy()) as session:
            resp = session.get(url, timeout=_HTTP_TIMEOUT, headers=headers)
    except requests.RequestException as exc:
        raise SpotifyResolutionError(f"{label} fetch failed: {exc}") from exc
    if resp.status_code != 200:
        raise SpotifyResolutionError(f"{label} returned HTTP {resp.status_code}", status_code=resp.status_code)
    return resp


def fetch_page(url: str) -> str:
    """GET a Spotify page and return its server-rendered HTML (guarded, non-browser UA)."""
    return str(_guarded_get(url, label="Spotify page", user_agent=_SPOTIFY_PAGE_USER_AGENT).text)


def expand_short_link(url: str) -> str:
    """Follow a ``spotify.link`` short link and return the final URL."""
    return str(_guarded_get(url, label="Spotify short link", user_agent=_SPOTIFY_PAGE_USER_AGENT).url)


def itunes_search(params: str) -> list:
    """``itunes.apple.com/search?<params>`` → ``results`` list."""
    return _itunes_json(f"https://itunes.apple.com/search?{params}", label="iTunes search")


def itunes_lookup(params: str) -> list:
    """``itunes.apple.com/lookup?<params>`` → ``results`` list."""
    return _itunes_json(f"https://itunes.apple.com/lookup?{params}", label="iTunes lookup")


def _itunes_json(url: str, *, label: str) -> list:
    resp = _guarded_get(url, label=label)
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SpotifyResolutionError(f"{label} returned non-JSON: {exc}") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    return results if isinstance(results, list) else []


def fetch_feed(url: str) -> bytes:
    """GET a show's RSS feed (guarded)."""
    return bytes(_guarded_get(url, label="Show RSS feed").content)


# ---------------------------------------------------------------------------
# Spotify metadata — page scrape
# ---------------------------------------------------------------------------

_META_TAG_RE = re.compile(r"<meta\s[^>]{0,2000}>", re.IGNORECASE)
_META_ATTR_RE = re.compile(r"""([A-Za-z:_-]{1,40})\s*=\s*(?:"([^"]{0,4000})"|'([^']{0,4000})')""")
_TITLE_TAG_RE = re.compile(r"<title[^>]{0,200}>([^<]{0,1000})</title>", re.IGNORECASE)
_PUBLISHER_JSON_RE = re.compile(r'"publisher"\s*:\s*"([^"\\]{1,200})"')
_LISTEN_TO_EPISODE_RE = re.compile(r"^Listen to this episode from (.+?) on Spotify\.?", re.IGNORECASE | re.DOTALL)
_LISTEN_TO_SHOW_RE = re.compile(r"^Listen to (.+?) on Spotify\.?", re.IGNORECASE | re.DOTALL)
_SPOTIFY_TITLE_SUFFIX_RE = re.compile(r"\s*[|\-–—]\s*(?:Podcast on Spotify|Spotify)\s*$", re.IGNORECASE)


def parse_meta_tags(page_html: str) -> Dict[str, str]:
    """Return ``{property-or-name: content}`` for the page's ``<meta>`` tags.

    First occurrence wins. Keys are lower-cased; values are HTML-unescaped
    and whitespace-trimmed. Never raises.
    """
    tags: Dict[str, str] = {}
    for match in _META_TAG_RE.finditer(page_html[:_MAX_PAGE_SCAN_BYTES]):
        attrs: Dict[str, str] = {}
        for attr in _META_ATTR_RE.finditer(match.group(0)):
            value = attr.group(2) if attr.group(2) is not None else attr.group(3)
            attrs[attr.group(1).lower()] = html.unescape(value or "").strip()
        key = attrs.get("property") or attrs.get("name")
        content = attrs.get("content")
        if key and content is not None and key.lower() not in tags:
            tags[key.lower()] = content
    return tags


def _page_title(page_html: str) -> str:
    match = _TITLE_TAG_RE.search(page_html[:_MAX_PAGE_SCAN_BYTES])
    title = html.unescape(match.group(1)).strip() if match else ""
    # The JS shell's generic title is not a show name — treating it as one
    # would send "Spotify – Web Player" to the Apple search.
    return "" if normalise_title(title) == _SPOTIFY_SHELL_TITLE else title


def _strip_spotify_title_suffix(text: str) -> str:
    return _SPOTIFY_TITLE_SUFFIX_RE.sub("", text).strip()


def _split_dot_separated(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"\s+[·•]\s+", text) if part.strip()]


def _parse_iso_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_int(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if raw > 0 else None
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        return value if value > 0 else None
    return None


def _page_publisher(page_html: str) -> Optional[str]:
    match = _PUBLISHER_JSON_RE.search(page_html)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def parse_episode_page(page_html: str, episode_id: str) -> SpotifyEpisodeMetadata:
    """Build :class:`SpotifyEpisodeMetadata` from an ``open.spotify.com/episode`` page.

    Every field is read defensively — Spotify's markup is unversioned. The
    title and show name are required; anything else missing degrades the
    match score rather than failing the import.
    """
    tags = parse_meta_tags(page_html)
    title = tags.get("og:title") or tags.get("twitter:title") or ""
    page_title = _strip_spotify_title_suffix(_page_title(page_html))
    og_description = tags.get("og:description") or tags.get("twitter:description") or ""

    show_name = ""
    # ``<title>`` is "<episode> - <show> | Podcast on Spotify".
    if page_title:
        if title and page_title.lower().startswith(title.lower()):
            remainder = page_title[len(title) :].strip()
            remainder = re.sub(r"^[\-–—|:]\s*", "", remainder).strip()
            show_name = remainder
        elif " - " in page_title:
            head, _, tail = page_title.rpartition(" - ")
            show_name = tail.strip()
            title = title or head.strip()
        elif not title:
            title = page_title
    # ``og:description`` is "<show> · Episode" on episode pages, or the
    # older "Listen to this episode from <show> on Spotify. <description>".
    description = ""
    if og_description:
        listen = _LISTEN_TO_EPISODE_RE.match(og_description)
        parts = _split_dot_separated(og_description)
        if listen:
            show_name = show_name or listen.group(1).strip()
            description = og_description[listen.end() :].strip()
        elif len(parts) >= 2 and parts[-1].lower() == "episode":
            show_name = show_name or parts[0]
        else:
            description = og_description
    plain_description = tags.get("description")
    if plain_description and (not description or len(plain_description) > len(description)):
        parts = _split_dot_separated(plain_description)
        if not (len(parts) >= 2 and parts[-1].lower() == "episode"):
            description = plain_description

    if not title or not show_name:
        raise SpotifyResolutionError(
            "Spotify's episode page did not expose the episode title and show name "
            "(Spotify may have changed its page layout). Paste the Apple Podcasts "
            "or RSS link for the episode instead."
        )
    return SpotifyEpisodeMetadata(
        episode_id=episode_id,
        title=title,
        show_name=show_name,
        publisher=_page_publisher(page_html),
        description=description,
        release_date=_parse_iso_datetime(tags.get("music:release_date")),
        duration_seconds=_parse_int(tags.get("music:duration")),
        image_url=tags.get("og:image") or None,
        source="page",
    )


def parse_show_page(page_html: str, show_id: str) -> SpotifyShowMetadata:
    """Build :class:`SpotifyShowMetadata` from an ``open.spotify.com/show`` page."""
    tags = parse_meta_tags(page_html)
    name = tags.get("og:title") or tags.get("twitter:title") or _strip_spotify_title_suffix(_page_title(page_html))
    og_description = tags.get("og:description") or tags.get("twitter:description") or ""
    publisher: Optional[str] = None
    description = ""
    if og_description:
        listen = _LISTEN_TO_SHOW_RE.match(og_description)
        parts = _split_dot_separated(og_description)
        if listen:
            name = name or listen.group(1).strip()
            description = og_description[listen.end() :].strip()
        elif len(parts) >= 2 and parts[0].lower() == "podcast":
            # "Podcast · <publisher>"
            publisher = parts[1]
        else:
            description = og_description
    plain_description = tags.get("description")
    if plain_description and len(plain_description) > len(description):
        description = plain_description
    if not name:
        raise SpotifyResolutionError(
            "Spotify's show page did not expose the show name (Spotify may have "
            "changed its page layout). Paste the show's RSS feed or Apple Podcasts link instead."
        )
    return SpotifyShowMetadata(
        show_id=show_id,
        name=name,
        publisher=publisher or _page_publisher(page_html),
        description=description,
        image_url=tags.get("og:image") or None,
        source="page",
    )


# ---------------------------------------------------------------------------
# Spotify metadata — Web API (optional, client-credentials)
# ---------------------------------------------------------------------------


def spotify_api_credentials() -> Optional[Tuple[str, str]]:
    """``(client_id, client_secret)`` from the environment, or None when unset."""
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return client_id, client_secret
    return None


class SpotifyWebApi:
    """Minimal client-credentials wrapper around the two endpoints we need."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: Optional[str] = None

    def _fetch_token(self) -> str:
        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode("utf-8")).decode("ascii")
        try:
            with guarded_session(user_agent="Thestill/1.0", retries=_retry_policy()) as session:
                resp = session.post(
                    _SPOTIFY_TOKEN_URL,
                    data={"grant_type": "client_credentials"},
                    headers={"Authorization": f"Basic {basic}"},
                    timeout=_HTTP_TIMEOUT,
                )
        except requests.RequestException as exc:
            raise SpotifyResolutionError(f"Spotify token request failed: {exc}") from exc
        if resp.status_code != 200:
            raise SpotifyResolutionError(f"Spotify token request returned HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SpotifyResolutionError(f"Spotify token response was not JSON: {exc}") from exc
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise SpotifyResolutionError("Spotify token response carried no access_token")
        return token

    def _get(self, path: str) -> dict:
        if self._token is None:
            self._token = self._fetch_token()
        resp = _guarded_get(
            f"{_SPOTIFY_API_BASE}{path}",
            label="Spotify API",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SpotifyResolutionError(f"Spotify API returned non-JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SpotifyResolutionError("Spotify API returned an unexpected payload")
        return payload

    def _get_with_market_fallback(self, path: str) -> dict:
        # ``market`` is required for client-credentials tokens; US first, then
        # unrestricted, so a region-locked entity still resolves. Only a
        # "not available here" answer is worth the second call — a 429 / 5xx
        # has already been retried by the session and would fail again.
        try:
            return self._get(f"{path}?market=US")
        except SpotifyResolutionError as exc:
            if exc.status_code not in (400, 404):
                raise
            return self._get(path)

    def episode(self, episode_id: str) -> SpotifyEpisodeMetadata:
        payload = self._get_with_market_fallback(f"/episodes/{episode_id}")
        return episode_metadata_from_api(payload, episode_id)

    def show(self, show_id: str) -> SpotifyShowMetadata:
        payload = self._get_with_market_fallback(f"/shows/{show_id}")
        return show_metadata_from_api(payload, show_id)


def _api_image(payload: dict) -> Optional[str]:
    images = payload.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and isinstance(first.get("url"), str):
            return str(first["url"])
    return None


def _api_release_date(payload: dict) -> Optional[datetime]:
    raw = payload.get("release_date")
    if not isinstance(raw, str) or not raw:
        return None
    # ``release_date_precision`` is "day" | "month" | "year"; pad to a date.
    parts = raw.split("-")
    while len(parts) < 3:
        parts.append("01")
    return _parse_iso_datetime("-".join(parts[:3]) + "T00:00:00+00:00")


def episode_metadata_from_api(payload: dict, episode_id: str) -> SpotifyEpisodeMetadata:
    """Map a Web API ``/v1/episodes/{id}`` payload to :class:`SpotifyEpisodeMetadata`."""
    show = payload.get("show") if isinstance(payload.get("show"), dict) else {}
    title = payload.get("name")
    show_name = show.get("name")
    if not isinstance(title, str) or not title or not isinstance(show_name, str) or not show_name:
        raise SpotifyResolutionError("Spotify API episode payload is missing the episode or show name")
    duration_ms = payload.get("duration_ms")
    publisher = show.get("publisher")
    return SpotifyEpisodeMetadata(
        episode_id=episode_id,
        title=title,
        show_name=show_name,
        publisher=publisher if isinstance(publisher, str) and publisher else None,
        description=payload.get("description") if isinstance(payload.get("description"), str) else "",
        release_date=_api_release_date(payload),
        duration_seconds=int(duration_ms / 1000) if isinstance(duration_ms, (int, float)) and duration_ms > 0 else None,
        image_url=_api_image(payload) or _api_image(show),
        source="api",
    )


def show_metadata_from_api(payload: dict, show_id: str) -> SpotifyShowMetadata:
    """Map a Web API ``/v1/shows/{id}`` payload to :class:`SpotifyShowMetadata`."""
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise SpotifyResolutionError("Spotify API show payload is missing the show name")
    publisher = payload.get("publisher")
    return SpotifyShowMetadata(
        show_id=show_id,
        name=name,
        publisher=publisher if isinstance(publisher, str) and publisher else None,
        description=payload.get("description") if isinstance(payload.get("description"), str) else "",
        image_url=_api_image(payload),
        source="api",
    )


# ---------------------------------------------------------------------------
# Text normalisation + similarity (stdlib only — rapidfuzz is an optional extra)
# ---------------------------------------------------------------------------

# "Ep. 12 –", "Episode 12:", "#12", "No. 12", "S2 E4 -" and a bare "12:" /
# "12 -" are dropped; a bare leading number with no separator ("2024 Year in
# Review") is content and stays. The number itself is kept separately (see
# :func:`episode_number`) — it is the only thing telling "Episode 12: Weekly
# update" from "Episode 13: Weekly update".
_EPISODE_PREFIX_RE = re.compile(
    r"^(?:(?P<keyword>(?:ep(?:isode)?\.?|#|no\.?|s\d{1,3}\s*e)\s*(?P<kw_num>\d{1,5})[a-z]?\s*[:.\-–—|)]?\s*)"
    r"|(?P<bare_num>\d{1,5})\s*[:.\-–—|)]\s*)",
    re.IGNORECASE,
)
# Unicode-aware: ``\w`` keeps letters / digits of every script, so Japanese
# or Cyrillic titles survive normalisation instead of collapsing to "".
_NON_WORD_RE = re.compile(r"[^\w\s]+|_+")
_FUZZY_TOKEN_FLOOR = 0.8


def _strip_latin_accents(char: str) -> str:
    # Accents are noise on Latin letters ("Café" / "Cafe") but part of the
    # letter elsewhere: stripping marks turns ジ into シ and й into и.
    decomposed = unicodedata.normalize("NFD", char)
    if len(decomposed) > 1 and unicodedata.name(decomposed[0], "").startswith("LATIN"):
        return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return char


def _fold(text: str) -> str:
    composed = unicodedata.normalize("NFKC", text)
    stripped = "".join(_strip_latin_accents(ch) for ch in composed)
    return stripped.lower().replace("&", " and ")


def _squash(text: str) -> str:
    return " ".join(_NON_WORD_RE.sub(" ", text).split())


def normalise_title(text: str) -> str:
    """Lower-case, strip accents / punctuation / emoji, drop ``Ep. 123 –`` prefixes.

    A title that is *only* an episode marker ("Episode 12") keeps it — an
    empty string would make every such title look identical.
    """
    if not text:
        return ""
    folded = _fold(text)
    return _squash(_EPISODE_PREFIX_RE.sub("", folded, count=1)) or _squash(folded)


def episode_number(text: str) -> Optional[Tuple[str, int]]:
    """``(style, number)`` for a leading episode marker, else None.

    ``style`` is ``"keyword"`` ("Ep. 12", "#12", "S2 E4") or ``"bare"``
    ("12: ..."). Callers only compare numbers of the same style: a bare
    number may be content ("1984: the book"), so it is never held against
    an explicit "Ep. 5".
    """
    match = _EPISODE_PREFIX_RE.match(_fold(text or "").strip())
    if not match:
        return None
    if match.group("kw_num") is not None:
        return "keyword", int(match.group("kw_num"))
    return "bare", int(match.group("bare_num"))


def _tokens(text: str) -> List[str]:
    return [tok for tok in normalise_title(text).split() if tok]


def token_set_ratio(left: str, right: str) -> float:
    """Order-insensitive token overlap in ``[0, 1]`` (a soft Jaccard index).

    Identical token sets score 1.0. Tokens present on one side only *lower*
    the score — deliberately unlike fuzzywuzzy's ``token_set_ratio``, which
    scores any subset as a perfect match and so cannot tell "The Daily"
    from "The Daily Show: Ears Edition". Near-identical tokens (typos,
    plurals) count as a partial match.
    """
    left_tokens, right_tokens = set(_tokens(left)), set(_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens & right_tokens
    matched = float(len(shared))
    right_rest = sorted(right_tokens - shared)
    for token in sorted(left_tokens - shared):
        best_ratio, best_other = 0.0, None
        for other in right_rest:
            ratio = SequenceMatcher(None, token, other).ratio()
            if ratio > best_ratio:
                best_ratio, best_other = ratio, other
        if best_other is not None and best_ratio >= _FUZZY_TOKEN_FLOOR:
            matched += best_ratio
            right_rest.remove(best_other)
    fuzzy_pairs = len(right_tokens - shared) - len(right_rest)
    union = len(left_tokens | right_tokens) - fuzzy_pairs
    return matched / union


def _title_similarity(left: str, right: str) -> float:
    """Token overlap blended with a whole-string ratio (rescues spacing / spelling variants)."""
    left_norm, right_norm = normalise_title(left), normalise_title(right)
    if not left_norm or not right_norm:
        return 0.0
    set_ratio = token_set_ratio(left, right)
    seq_ratio = SequenceMatcher(None, left_norm, right_norm).ratio()
    return max(set_ratio, 0.6 * set_ratio + 0.4 * seq_ratio)


# ---------------------------------------------------------------------------
# Apple show match
# ---------------------------------------------------------------------------

SHOW_ACCEPT_THRESHOLD = 0.75
_SHOW_SEARCH_LIMIT = 25
_SHOW_AMBIGUITY_MARGIN = 0.01
# Tied feeds tried for an episode link — each costs up to three fetches.
_SHOW_MAX_TIED = 3


def score_show_candidate(show_name: str, publisher: Optional[str], candidate: dict) -> float:
    """Fuzzy score of an iTunes ``podcast`` result against a Spotify show."""
    name_score = _title_similarity(show_name, str(candidate.get("collectionName") or ""))
    if not publisher:
        return name_score
    publisher_score = _title_similarity(publisher, str(candidate.get("artistName") or ""))
    # Publisher is a soft signal: Apple's ``artistName`` is often the host
    # rather than the network, so a poor publisher match must not sink an
    # exact name match.
    return 0.75 * name_score + 0.25 * max(publisher_score, 0.5 * name_score)


def _show_search_terms(show_name: str) -> List[str]:
    terms = [show_name]
    # Drop a subtitle after ":" / " - " / "|" (e.g. "The Rest Is Politics: US").
    simplified = re.split(r"\s*[:|]\s*|\s+[-–—]\s+", show_name, maxsplit=1)[0].strip()
    if simplified and simplified.lower() != show_name.lower():
        terms.append(simplified)
    return terms


def _apple_show_match(entry: dict, show_name: str, score: float) -> AppleShowMatch:
    return AppleShowMatch(
        collection_id=str(entry.get("collectionId") or ""),
        name=str(entry.get("collectionName") or show_name),
        feed_url=str(entry["feedUrl"]),
        publisher=str(entry.get("artistName") or ""),
        image_url=entry.get("artworkUrl600") or entry.get("artworkUrl100"),
        score=round(score, 3),
    )


def find_apple_show_candidates(
    show_name: str,
    publisher: Optional[str],
    *,
    search: Callable[[str], list] = itunes_search,
) -> List[AppleShowMatch]:
    """The best Apple directory entry for a Spotify show, plus any other feed it ties with.

    Usually one entry. More than one means the name (+ publisher) cannot
    separate them — "The Daily" is both the public feed and a subscriber
    feed — and the caller needs another signal: the episode match for an
    episode link, nothing for a show link (see :func:`find_apple_show`).
    Entries keep Apple's relevance order. Raises when nothing clears
    :data:`SHOW_ACCEPT_THRESHOLD`.
    """
    # One entry per feed: (score, exact-name flag, candidate). An exact name
    # outranks an equal-scoring near match.
    wanted = normalise_title(show_name)
    by_feed: Dict[str, Tuple[float, bool, dict]] = {}
    for term in _show_search_terms(show_name):
        params = f"term={quote_plus(term)}&media=podcast&entity=podcast&limit={_SHOW_SEARCH_LIMIT}"
        for candidate in search(params):
            if not isinstance(candidate, dict) or not candidate.get("feedUrl"):
                continue
            feed_url = str(candidate["feedUrl"])
            score = score_show_candidate(show_name, publisher, candidate)
            if feed_url not in by_feed or score > by_feed[feed_url][0]:
                exact = normalise_title(str(candidate.get("collectionName") or "")) == wanted
                by_feed[feed_url] = (score, exact, candidate)
        if any(score >= 0.9 for score, _, _ in by_feed.values()):
            break
    ranked = sorted(
        by_feed.values(),
        key=lambda item: item[0] + (_SHOW_AMBIGUITY_MARGIN if item[1] else 0.0),
        reverse=True,
    )
    if not ranked or ranked[0][0] < SHOW_ACCEPT_THRESHOLD:
        logger.info(
            "spotify_show_unmatched",
            show_name=show_name,
            publisher=publisher,
            best_score=round(ranked[0][0], 3) if ranked else None,
            best_candidate=ranked[0][2].get("collectionName") if ranked else None,
        )
        raise SpotifyResolutionError(
            f"Could not find “{show_name}” in the Apple Podcasts directory. If this show "
            "is a Spotify exclusive it has no public feed to import from; otherwise paste "
            "the show's RSS feed or its Apple Podcasts link instead."
        )
    best_score, best_exact, _ = ranked[0]
    tied = [
        item
        for item in ranked[:_SHOW_MAX_TIED]
        if item[1] == best_exact and best_score - item[0] < _SHOW_AMBIGUITY_MARGIN
    ]
    return [_apple_show_match(entry, show_name, score) for score, _, entry in tied]


def find_apple_show(
    show_name: str,
    publisher: Optional[str],
    *,
    search: Callable[[str], list] = itunes_search,
) -> AppleShowMatch:
    """Resolve a Spotify show name (+ publisher) to one Apple directory entry with a feed URL.

    Two different feeds that tie are reported as ambiguous rather than
    resolved by whichever one Apple happened to list first.
    """
    candidates = find_apple_show_candidates(show_name, publisher, search=search)
    if len(candidates) > 1:
        logger.info(
            "spotify_show_ambiguous",
            show_name=show_name,
            publisher=publisher,
            candidates=[f"{c.name} ({c.publisher})" for c in candidates],
            score=candidates[0].score,
        )
        raise SpotifyResolutionError(
            f"More than one show in the Apple Podcasts directory matches “{show_name}” equally "
            "well, so the right feed can't be picked safely. Paste the show's RSS feed or its "
            "Apple Podcasts link instead."
        )
    match = candidates[0]
    logger.info(
        "spotify_show_matched",
        show_name=show_name,
        publisher=publisher,
        collection_id=match.collection_id,
        collection_name=match.name,
        score=match.score,
    )
    return match


# ---------------------------------------------------------------------------
# Episode match
# ---------------------------------------------------------------------------

EPISODE_ACCEPT_THRESHOLD = 0.72
_DATE_TIGHT_HOURS = 36.0
_DATE_LOOSE_HOURS = 24.0 * 7
_DURATION_TIGHT_SECONDS = 120
_DURATION_LOOSE_SECONDS = 600
_AMBIGUITY_MARGIN = 0.01
_EPISODE_NUMBER_MISMATCH_FACTOR = 0.5


def _date_score(left: Optional[datetime], right: Optional[datetime]) -> float:
    if left is None or right is None:
        return 0.5
    hours = abs((left - right).total_seconds()) / 3600.0
    if hours <= _DATE_TIGHT_HOURS:
        return 1.0
    if hours <= _DATE_LOOSE_HOURS:
        # Linear decay from 1.0 at 36 h to 0.3 at 7 days.
        span = _DATE_LOOSE_HOURS - _DATE_TIGHT_HOURS
        return 1.0 - 0.7 * ((hours - _DATE_TIGHT_HOURS) / span)
    return 0.0


def _duration_score(left: Optional[int], right: Optional[int]) -> float:
    if not left or not right:
        return 0.5
    delta = abs(left - right)
    if delta <= _DURATION_TIGHT_SECONDS:
        return 1.0
    if delta <= _DURATION_LOOSE_SECONDS:
        return 0.6
    return 0.2


def score_episode_candidate(meta: SpotifyEpisodeMetadata, candidate: EpisodeCandidate) -> EpisodeScore:
    """Score one candidate against the Spotify metadata (see module docstring)."""
    title = _title_similarity(meta.title, candidate.title)
    wanted_number, candidate_number = episode_number(meta.title), episode_number(candidate.title)
    if wanted_number and candidate_number and wanted_number[0] == candidate_number[0]:
        if wanted_number[1] != candidate_number[1]:
            # Same numbering style, different number: a different episode,
            # however alike the rest of the title ("Episode 13: Weekly update").
            title *= _EPISODE_NUMBER_MISMATCH_FACTOR
    date = _date_score(meta.release_date, candidate.pub_date)
    duration = _duration_score(meta.duration_seconds, candidate.duration_seconds)
    combined = 0.6 * title + 0.25 * date + 0.15 * duration
    accepted = (combined >= EPISODE_ACCEPT_THRESHOLD and title >= 0.5) or (
        title >= 0.95 and (date >= 0.3 or duration >= 0.6)
    )
    return EpisodeScore(
        title=round(title, 3),
        date=round(date, 3),
        duration=round(duration, 3),
        combined=round(combined, 3),
        accepted=accepted,
    )


def _same_episode(left: EpisodeCandidate, right: EpisodeCandidate) -> bool:
    """True when two candidates are one episode listed twice (same enclosure or id)."""
    if left.audio_url == right.audio_url:
        return True
    return bool(left.external_id) and left.external_id == right.external_id


def pick_episode(
    meta: SpotifyEpisodeMetadata, candidates: Sequence[EpisodeCandidate]
) -> Optional[Tuple[EpisodeCandidate, EpisodeScore]]:
    """Return the best accepted candidate, or None when nothing clears the bar.

    Two accepted candidates within :data:`_AMBIGUITY_MARGIN` of each other
    (a "Part 1" / "Part 2" pair with identical dates, or two same-titled
    daily episodes a day apart) are treated as unresolvable rather than
    picked arbitrarily — unless they are the same episode listed twice.
    """
    scored = [(cand, score_episode_candidate(meta, cand)) for cand in candidates if cand.audio_url]
    scored.sort(key=lambda pair: pair[1].combined, reverse=True)
    if not scored or not scored[0][1].accepted:
        return None
    if len(scored) > 1 and scored[1][1].accepted:
        gap = scored[0][1].combined - scored[1][1].combined
        if gap < _AMBIGUITY_MARGIN and not _same_episode(scored[0][0], scored[1][0]):
            logger.info(
                "spotify_episode_ambiguous",
                spotify_episode_id=meta.episode_id,
                first=scored[0][0].title,
                second=scored[1][0].title,
                score=scored[0][1].combined,
            )
            return None
    return scored[0]


def candidates_from_itunes(results: list, collection_id: str) -> List[EpisodeCandidate]:
    """Map iTunes ``podcastEpisode`` entries (window or search) to candidates."""
    out: List[EpisodeCandidate] = []
    for entry in results:
        if not isinstance(entry, dict) or entry.get("wrapperType") != "podcastEpisode":
            continue
        if collection_id and str(entry.get("collectionId") or "") != str(collection_id):
            continue
        audio = entry.get("episodeUrl") or entry.get("previewUrl")
        if not isinstance(audio, str) or not audio:
            continue
        millis = entry.get("trackTimeMillis")
        out.append(
            EpisodeCandidate(
                title=str(entry.get("trackName") or ""),
                audio_url=audio,
                pub_date=_parse_iso_datetime(entry.get("releaseDate")),
                duration_seconds=int(millis / 1000) if isinstance(millis, (int, float)) and millis > 0 else None,
                external_id=str(entry.get("trackId") or ""),
                description=str(entry.get("description") or ""),
                image_url=entry.get("artworkUrl600") or entry.get("artworkUrl160") or entry.get("artworkUrl100"),
                origin="itunes",
            )
        )
    return out


def candidates_from_feed(raw_feed: bytes) -> List[EpisodeCandidate]:
    """Map RSS items to candidates (full history — the deep fallback)."""
    parsed = feedparser.parse(raw_feed)
    out: List[EpisodeCandidate] = []
    for entry in getattr(parsed, "entries", None) or []:
        audio_url: Optional[str] = None
        for link in entry.get("links") or []:
            href = link.get("href")
            if link.get("rel") == "enclosure" and isinstance(href, str) and href:
                audio_url = href
                break
        if not audio_url:
            continue
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        pub_date = parse_struct_time_utc(published) if published else None
        image = entry.get("image")
        image_url = image.get("href") if isinstance(image, dict) else None
        out.append(
            EpisodeCandidate(
                title=str(entry.get("title") or ""),
                audio_url=audio_url,
                pub_date=pub_date,
                duration_seconds=parse_duration(entry.get("itunes_duration")),
                external_id=str(entry.get("id") or ""),
                description=str(entry.get("summary") or ""),
                image_url=image_url,
                origin="rss",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

EpisodeMetadataFetcher = Callable[[str, str], SpotifyEpisodeMetadata]
"""``(episode_id, page_url) -> metadata``. Injected by tests."""
ShowMetadataFetcher = Callable[[str, str], SpotifyShowMetadata]
"""``(show_id, page_url) -> metadata``. Injected by tests."""


def _canonical_page_url(kind: str, entity_id: str) -> str:
    return f"https://open.spotify.com/{kind}/{entity_id}"


def default_episode_metadata(episode_id: str, page_url: str) -> SpotifyEpisodeMetadata:
    """Web API when credentials are configured (page scrape as fallback), else page scrape."""
    creds = spotify_api_credentials()
    if creds:
        try:
            return SpotifyWebApi(*creds).episode(episode_id)
        except SpotifyResolutionError as exc:
            logger.warning("spotify_api_episode_failed", spotify_episode_id=episode_id, error=str(exc))
    return parse_episode_page(fetch_page(page_url), episode_id)


def default_show_metadata(show_id: str, page_url: str) -> SpotifyShowMetadata:
    """Web API when credentials are configured (page scrape as fallback), else page scrape."""
    creds = spotify_api_credentials()
    if creds:
        try:
            return SpotifyWebApi(*creds).show(show_id)
        except SpotifyResolutionError as exc:
            logger.warning("spotify_api_show_failed", spotify_show_id=show_id, error=str(exc))
    return parse_show_page(fetch_page(page_url), show_id)


class SpotifyLinkResolver:
    """Spotify link → (Apple show + feed URL, matched episode).

    All collaborators default to the live implementations above; tests pass
    canned callables. ``resolve_episode`` powers the inbox import,
    ``resolve_show`` powers "add podcast" from a Spotify show link.
    """

    def __init__(
        self,
        *,
        episode_metadata: EpisodeMetadataFetcher = default_episode_metadata,
        show_metadata: ShowMetadataFetcher = default_show_metadata,
        search: Callable[[str], list] = itunes_search,
        lookup: Callable[[str], list] = itunes_lookup,
        feed: Callable[[str], bytes] = fetch_feed,
        expand: Callable[[str], str] = expand_short_link,
    ) -> None:
        self._episode_metadata = episode_metadata
        self._show_metadata = show_metadata
        self._search = search
        self._lookup = lookup
        self._feed = feed
        self._expand = expand

    # -- URL handling ------------------------------------------------------

    def identify(self, url: str) -> Tuple[str, str]:
        """``(kind, id)`` for the pasted link, following ``spotify.link`` shorteners."""
        entity = extract_spotify_entity(url)
        if entity is None and is_spotify_short_link(url):
            expanded = self._expand(url.strip())
            entity = extract_spotify_entity(expanded)
        if entity is None:
            raise SpotifyResolutionError(
                "Spotify link does not point to an episode or show. Paste the link from "
                "Spotify's Share menu (open.spotify.com/episode/... or /show/...)."
            )
        return entity

    # -- Episode import ----------------------------------------------------

    def resolve_episode(self, url: str) -> ResolvedSpotifyEpisode:
        kind, entity_id = self.identify(url)
        if kind != "episode":
            raise SpotifyResolutionError(
                "This is a Spotify show link, not an episode. To import one episode paste "
                "its episode link; to follow the show, add it from the Podcasts page."
            )
        meta = self._episode_metadata(entity_id, _canonical_page_url("episode", entity_id))
        logger.info(
            "spotify_metadata_fetched",
            spotify_episode_id=entity_id,
            source=meta.source,
            title=meta.title,
            show_name=meta.show_name,
            has_release_date=meta.release_date is not None,
            has_duration=meta.duration_seconds is not None,
        )
        # Same-named feeds ("The Daily" public + subscriber) are settled by
        # the episode itself: the feed that carries it wins, best score first,
        # Apple's relevance order on a tie.
        shows = find_apple_show_candidates(meta.show_name, meta.publisher, search=self._search)
        matches = [(show, picked) for show in shows if (picked := self._match_episode(meta, show)) is not None]
        if not matches:
            raise SpotifyResolutionError(
                f"Found “{shows[0].name}” in the Apple Podcasts directory but could not confidently "
                f"match the episode “{meta.title}” in its feed. Paste the Apple Podcasts episode "
                "link or the show's RSS feed instead."
            )
        show, (candidate, score) = max(matches, key=lambda pair: pair[1][1].combined)
        logger.info(
            "spotify_episode_matched",
            spotify_episode_id=entity_id,
            collection_id=show.collection_id,
            matched_title=candidate.title,
            matched_via=candidate.origin,
            score=score.combined,
            title_score=score.title,
            date_score=score.date,
            duration_score=score.duration,
        )
        return ResolvedSpotifyEpisode(episode_id=entity_id, spotify=meta, show=show, episode=candidate, score=score)

    def _match_episode(
        self, meta: SpotifyEpisodeMetadata, show: AppleShowMatch
    ) -> Optional[Tuple[EpisodeCandidate, EpisodeScore]]:
        tried: List[str] = []
        # Tier 1 — the show's newest 200 on iTunes (cheap JSON; covers most
        # pasted links, which are recent).
        if show.collection_id:
            tried.append("itunes_window")
            try:
                window = self._lookup(f"id={show.collection_id}&entity=podcastEpisode&limit=200")
            except SpotifyResolutionError as exc:
                logger.warning("spotify_itunes_window_failed", collection_id=show.collection_id, error=str(exc))
                window = []
            picked = pick_episode(meta, candidates_from_itunes(window, show.collection_id))
            if picked:
                return picked
            # Tier 2 — episode-title search scoped to the show (reaches past
            # the 200-episode window for well-indexed shows).
            tried.append("itunes_search")
            try:
                found = self._search(f"term={quote_plus(meta.title)}&media=podcast&entity=podcastEpisode&limit=50")
            except SpotifyResolutionError as exc:
                logger.warning("spotify_itunes_search_failed", collection_id=show.collection_id, error=str(exc))
                found = []
            picked = pick_episode(meta, candidates_from_itunes(found, show.collection_id))
            if picked:
                return picked
        # Tier 3 — the RSS feed itself: full history, and the enclosure URL
        # is by definition what the feed refresh will see later.
        tried.append("rss")
        try:
            picked = pick_episode(meta, candidates_from_feed(self._feed(show.feed_url)))
        except SpotifyResolutionError as exc:
            logger.warning("spotify_feed_fetch_failed", feed_url=show.feed_url, error=str(exc))
            picked = None
        if picked is None:
            logger.info(
                "spotify_episode_unmatched",
                spotify_episode_id=meta.episode_id,
                collection_id=show.collection_id,
                title=meta.title,
                tried=tried,
            )
        return picked

    # -- Show → feed (add podcast) ----------------------------------------

    def resolve_show(self, url: str) -> AppleShowMatch:
        """Feed URL for a Spotify show (or the show behind a Spotify episode link)."""
        kind, entity_id = self.identify(url)
        if kind == "show":
            show_meta = self._show_metadata(entity_id, _canonical_page_url("show", entity_id))
            name, publisher = show_meta.name, show_meta.publisher
        else:
            episode_meta = self._episode_metadata(entity_id, _canonical_page_url("episode", entity_id))
            name, publisher = episode_meta.show_name, episode_meta.publisher
        logger.info("spotify_metadata_fetched", spotify_kind=kind, spotify_id=entity_id, show_name=name)
        return find_apple_show(name, publisher, search=self._search)
