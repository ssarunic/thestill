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
Find a show's YouTube channel by search, verified by its uploads (spec #87, Phase 3d).

A search result is never taken on its name: namesake channels are common
("How I AI" returns four). Each result's uploads are listed and matched
against the show's newest episodes with the episode linker, and a channel
is accepted only when it matches at least :data:`MIN_CHANNEL_MATCHES` of
them and the runner-up at most :data:`MAX_RUNNER_UP_SHARE` as many.

A result whose listing failed is **unverified**, never a zero: it could be
the real channel, and scoring it 0 would let a channel that cross-posts a
few of the show's episodes (a guest's channel, a network channel) pass the
runner-up guard. Any unverified result defers the attempt. A channel
without a ``/videos`` tab (auto-generated "Topic" channels, shorts-only
clip channels) is verified with no uploads: yt-dlp says so outright.

This module searches and decides; listing and matching are the service's
(the same injected listing it uses for the episode pass).
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from .publisher_links import GENERIC_YOUTUBE_CHANNELS

SEARCH_RESULTS = 5
VERIFY_EPISODES = 20  # newest episodes of the show matched against each result
VERIFY_LISTING_LIMIT = 150  # uploads listed per result
MIN_CHANNEL_MATCHES = 3  # UnHerd matched 2 of The Econoclasts' episodes through guest cross-posts
MAX_RUNNER_UP_SHARE = 1 / 3
# A chart channel is "demonstrably wrong" only when it matched none of a
# sample at least this large.
MIN_WRONG_CHANNEL_SAMPLE = 8

# YouTube's "Channels" filter on the results page.
_CHANNEL_FILTER = "EgIQAg%3D%3D"
# yt-dlp's message for a channel that has no long-form uploads at all.
_NO_VIDEOS_TAB = "does not have a videos tab"


@dataclass(frozen=True)
class ChannelHit:
    """One channel search result."""

    url: str  # the @handle URL when the result carries one, else /channel/<id>
    title: str
    aliases: Tuple[str, ...] = ()  # every URL naming this channel, for comparing with a stored link


@dataclass(frozen=True)
class ChannelScore:
    """How many of the show's sample episodes one result's uploads matched."""

    hit: ChannelHit
    matches: Optional[int]  # None: the listing failed, so the result is unverified


@dataclass(frozen=True)
class ChannelPick:
    """The accepted channel, or None and why not."""

    channel: Optional[ChannelHit]
    # 'accepted' | 'deferred' (an unverified result) | 'too_few' | 'ambiguous' | 'no_results'
    reason: str


def search_queries(title: str, author: Optional[str]) -> List[str]:
    """The title, then "title author" when the author adds something ("Possible" + "Reid Hoffman")."""
    title = (title or "").strip()
    author = (author or "").strip()
    queries = [title] if title else []
    if title and author and author.casefold() not in title.casefold():
        queries.append(f"{title} {author}")
    return queries


def search_channels(query: str, *, limit: int = SEARCH_RESULTS) -> List[ChannelHit]:
    """
    The top ``limit`` channels YouTube's search returns for ``query``.

    Raises whatever yt-dlp raises (network, bot check); the service treats
    an error as "search failed, try again next week".
    """
    import yt_dlp

    url = (
        "https://www.youtube.com/results?search_query="
        + urllib.parse.quote_plus(query)
        + "&sp="
        + urllib.parse.quote(_CHANNEL_FILTER)
    )
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": int(limit),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    return parse_search((info or {}).get("entries") or [])[:limit]


def parse_search(entries: Iterable[Any]) -> List[ChannelHit]:
    """Keep channel results; drop generic platform channels. Untrusted input."""
    hits: List[ChannelHit] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        channel_url = _youtube_url(raw.get("channel_url") or raw.get("url"))
        handle_url = _youtube_url(raw.get("uploader_url"))
        if channel_url is None or "/channel/" not in channel_url:
            continue  # a video or playlist result
        aliases = tuple(u for u in (handle_url, channel_url) if u)
        if any(_channel_path(u) in GENERIC_YOUTUBE_CHANNELS for u in aliases):
            continue
        title = raw.get("title") if isinstance(raw.get("title"), str) else ""
        hits.append(ChannelHit(url=handle_url or channel_url, title=title.strip(), aliases=aliases))
    return hits


def is_no_uploads_error(exc: BaseException) -> bool:
    """The listing failed because the channel has no ``/videos`` tab: verified, 0 uploads."""
    return _NO_VIDEOS_TAB in str(exc)


def same_channel(hit: ChannelHit, url: Optional[str]) -> bool:
    """Whether ``url`` (a stored show link) names ``hit``'s channel."""
    if not url:
        return False
    path = _channel_path(url)
    return any(_channel_path(alias) == path for alias in hit.aliases)


def pick_channel(scores: Sequence[ChannelScore]) -> ChannelPick:
    """Accept the best result only when every result was verified and it leads clearly."""
    if not scores:
        return ChannelPick(None, "no_results")
    if any(s.matches is None for s in scores):
        return ChannelPick(None, "deferred")
    ranked = sorted(scores, key=lambda s: s.matches or 0, reverse=True)
    best = ranked[0].matches or 0
    runner_up = (ranked[1].matches or 0) if len(ranked) > 1 else 0
    if best < MIN_CHANNEL_MATCHES:
        return ChannelPick(None, "too_few")
    if runner_up > best * MAX_RUNNER_UP_SHARE:
        return ChannelPick(None, "ambiguous")
    return ChannelPick(ranked[0].hit, "accepted")


def _youtube_url(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    parts = urllib.parse.urlsplit(value.strip())
    if parts.netloc.casefold() not in ("www.youtube.com", "youtube.com", "m.youtube.com") or not parts.path.strip("/"):
        return None
    return "https://www.youtube.com/" + parts.path.strip("/")


def _channel_path(url: str) -> str:
    """``@handle`` / ``channel/UC…`` / ``c/name``, case-folded, without a trailing tab."""
    path = url.split("youtube.com/", 1)[-1].split("?", 1)[0].strip("/")
    for tab in ("/videos", "/streams", "/podcasts", "/featured"):
        if path.casefold().endswith(tab):
            path = path[: -len(tab)]
    return path.casefold()
