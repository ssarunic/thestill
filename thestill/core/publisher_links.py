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
Platform links the publisher already put in the feed (spec #87, ``publisher``).

No network. For an episode, in order of trust:

1. the import that created it (``canonical_id`` ``spotify:<id>`` /
   ``youtube:<video id>``),
2. a ``video/youtube`` alternate enclosure (Podcasting 2.0, spec #62),
3. the item's ``<link>`` when it is a Spotify episode or YouTube video,
4. links in the description — accepted only when the description names
   exactly ONE episode / video on that platform. Show notes routinely link
   "last week's episode" or a guest's video; a unique link is the episode's
   own, two links are a guess we refuse to make.

For a show: a Spotify show link or a YouTube channel link in the podcast's
own description / website is taken as-is when unique; the same link
appearing in at least :data:`MIN_EPISODE_VOTES` episode descriptions is
accepted when no other show / channel competes with it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from ..models.podcast import AlternateEnclosure, PlatformLinkCandidate
from ..utils.url_patterns import (
    extract_spotify_entity,
    extract_youtube_video_id,
    find_spotify_entities,
    find_youtube_channel_urls,
    find_youtube_video_ids,
)

MIN_EPISODE_VOTES = 3

_YOUTUBE_MIME = "video/youtube"


@dataclass(frozen=True)
class PublisherLink:
    platform: str  # 'spotify' | 'youtube'
    url: str
    external_ref: str
    source: str  # 'import' | 'alternate_enclosure' | 'item_link' | 'description' — for log lines


@dataclass(frozen=True)
class ShowLinks:
    spotify_url: Optional[str] = None
    youtube_url: Optional[str] = None


def spotify_episode_url(episode_id: str) -> str:
    return f"https://open.spotify.com/episode/{episode_id}"


def spotify_show_url(show_id: str) -> str:
    return f"https://open.spotify.com/show/{show_id}"


def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def publisher_links_for_candidate(
    candidate: PlatformLinkCandidate,
    alternate_enclosures: Sequence[AlternateEnclosure] = (),
) -> Dict[str, PublisherLink]:
    """At most one link per platform, keyed by platform."""
    found: Dict[str, PublisherLink] = {}
    spotify = _spotify_for(candidate)
    if spotify:
        found["spotify"] = spotify
    youtube = _youtube_for(candidate, alternate_enclosures)
    if youtube:
        found["youtube"] = youtube
    return found


def _spotify_for(candidate: PlatformLinkCandidate) -> Optional[PublisherLink]:
    prefix, _, ident = (candidate.canonical_id or "").partition(":")
    if prefix == "spotify" and ident:
        return PublisherLink("spotify", spotify_episode_url(ident), ident, "import")
    if candidate.website_url:
        entity = extract_spotify_entity(candidate.website_url)
        if entity and entity[0] == "episode":
            return PublisherLink("spotify", spotify_episode_url(entity[1]), entity[1], "item_link")
    episodes = [sid for kind, sid in find_spotify_entities(candidate.description_html) if kind == "episode"]
    if len(episodes) == 1:
        return PublisherLink("spotify", spotify_episode_url(episodes[0]), episodes[0], "description")
    return None


def _youtube_for(
    candidate: PlatformLinkCandidate, alternate_enclosures: Sequence[AlternateEnclosure]
) -> Optional[PublisherLink]:
    prefix, _, ident = (candidate.canonical_id or "").partition(":")
    if prefix == "youtube" and ident:
        return PublisherLink("youtube", youtube_watch_url(ident), ident, "import")
    for alt in sorted(alternate_enclosures, key=lambda a: not a.is_default):
        if alt.mime_type.lower() != _YOUTUBE_MIME:
            continue
        video_id = extract_youtube_video_id(alt.source_uri)
        if video_id:
            return PublisherLink("youtube", youtube_watch_url(video_id), video_id, "alternate_enclosure")
    if candidate.website_url:
        video_id = extract_youtube_video_id(candidate.website_url)
        if video_id:
            return PublisherLink("youtube", youtube_watch_url(video_id), video_id, "item_link")
    ids = find_youtube_video_ids(candidate.description_html)
    if len(ids) == 1:
        return PublisherLink("youtube", youtube_watch_url(ids[0]), ids[0], "description")
    return None


def show_links_from_sources(
    podcast_texts: Iterable[Optional[str]],
    episode_texts: Iterable[Optional[str]],
) -> ShowLinks:
    """
    The show's own Spotify page / YouTube channel as the publisher states it.

    Podcast-level text wins when it names exactly one show / channel.
    Otherwise a show / channel named in at least :data:`MIN_EPISODE_VOTES`
    episode descriptions is accepted if it is the only one named at all.
    """
    podcast_blob = " ".join(t for t in podcast_texts if t)
    spotify = _unique(sid for kind, sid in find_spotify_entities(podcast_blob) if kind == "show")
    youtube = _unique(find_youtube_channel_urls(podcast_blob))

    if spotify is None or youtube is None:
        spotify_votes: Counter = Counter()
        youtube_votes: Counter = Counter()
        for text in episode_texts:
            if not text:
                continue
            for sid in {sid for kind, sid in find_spotify_entities(text) if kind == "show"}:
                spotify_votes[sid] += 1
            for url in set(find_youtube_channel_urls(text)):
                youtube_votes[url] += 1
        if spotify is None:
            spotify = _voted(spotify_votes)
        if youtube is None:
            youtube = _voted(youtube_votes)

    return ShowLinks(
        spotify_url=spotify_show_url(spotify) if spotify else None,
        youtube_url=youtube,
    )


def _unique(values: Iterable[str]) -> Optional[str]:
    distinct = list(dict.fromkeys(values))
    return distinct[0] if len(distinct) == 1 else None


def _voted(votes: Counter) -> Optional[str]:
    if len(votes) != 1:
        return None
    ((value, count),) = votes.items()
    return value if count >= MIN_EPISODE_VOTES else None


def describe_sources(links: Iterable[PublisherLink]) -> Dict[str, int]:
    """``{source: count}`` for log lines."""
    counts: Dict[str, int] = {}
    for link in links:
        counts[link.source] = counts.get(link.source, 0) + 1
    return counts


__all__: List[str] = [
    "MIN_EPISODE_VOTES",
    "PublisherLink",
    "ShowLinks",
    "publisher_links_for_candidate",
    "show_links_from_sources",
    "describe_sources",
]
