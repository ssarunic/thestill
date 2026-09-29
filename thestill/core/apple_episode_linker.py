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
Match feed episodes to Apple Podcasts episode pages (spec #87, Phase 1).

Pure functions over the iTunes lookup window
(``lookup?id=<collectionId>&entity=podcastEpisode&limit=200``): no HTTP,
no database. The window's entries carry the feed GUID (``episodeGuid``),
the enclosure URL (``episodeUrl``), the title and release date, and the
Apple episode page (``trackViewUrl``), so most links are exact GUID hits.

Match methods, first hit wins per candidate:

- ``guid``: ``episodeGuid`` equals the episode's ``external_id``.
- ``audio_url``: ``episodeUrl`` equals ``audio_url`` — exact, or with the
  query string dropped and the scheme normalised.
- ``title_date``: normalised titles equal and ``releaseDate`` within
  :data:`TITLE_DATE_TOLERANCE` of ``pub_date``. Skipped when the title is
  ambiguous inside the window, so a fuzzy method never picks between twins.

Each Apple entry links at most one episode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from ..models.podcast import PlatformLinkCandidate
from ..utils.url_patterns import extract_apple_podcast_id

# Feed ``pubDate`` and Apple's ``releaseDate`` for the same episode routinely
# differ by hours (timezone rounding, Apple's ingest time). A day and a half
# keeps weekly shows unambiguous while absorbing that drift.
TITLE_DATE_TOLERANCE = timedelta(hours=36)

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class AppleEpisodeEntry:
    """The subset of an iTunes ``podcastEpisode`` result the matcher reads."""

    track_id: str
    track_view_url: str
    guid: Optional[str]
    audio_url: Optional[str]
    title: Optional[str]
    release_date: Optional[datetime]


@dataclass(frozen=True)
class AppleEpisodeMatch:
    """One resolved (candidate, Apple entry) pair."""

    episode_id: str
    url: str
    external_ref: str
    match_method: str  # 'guid' | 'audio_url' | 'title_date'


def parse_lookup_window(results: Iterable[Any]) -> List[AppleEpisodeEntry]:
    """
    Keep the ``podcastEpisode`` entries of an iTunes lookup that carry an
    episode page. The show record (``wrapperType != 'podcastEpisode'``) and
    malformed entries are dropped — the response is untrusted input.
    """
    entries: List[AppleEpisodeEntry] = []
    for raw in results:
        if not isinstance(raw, dict) or raw.get("wrapperType") != "podcastEpisode":
            continue
        track_id = raw.get("trackId")
        url = raw.get("trackViewUrl")
        if track_id is None or not isinstance(url, str) or not url.startswith("https://"):
            continue
        entries.append(
            AppleEpisodeEntry(
                track_id=str(track_id),
                track_view_url=url,
                guid=_clean_str(raw.get("episodeGuid")),
                audio_url=_clean_str(raw.get("episodeUrl")),
                title=_clean_str(raw.get("trackName")),
                release_date=_parse_release_date(raw.get("releaseDate")),
            )
        )
    return entries


def apple_collection_id(apple_url: Optional[str]) -> Optional[str]:
    """The ``id<digits>`` collection id inside a show's Apple URL, or None."""
    if not apple_url:
        return None
    return extract_apple_podcast_id(apple_url)


def match_candidates(
    candidates: Sequence[PlatformLinkCandidate],
    entries: Sequence[AppleEpisodeEntry],
) -> List[AppleEpisodeMatch]:
    """
    Link candidates to window entries. Returns one match per linked
    candidate; unmatched candidates are simply absent (the caller records
    them as not found).
    """
    by_guid: Dict[str, AppleEpisodeEntry] = {}
    by_audio: Dict[str, AppleEpisodeEntry] = {}
    by_loose_audio: Dict[str, AppleEpisodeEntry] = {}
    by_title: Dict[str, List[AppleEpisodeEntry]] = {}
    for entry in entries:
        if entry.guid:
            by_guid.setdefault(entry.guid, entry)
        if entry.audio_url:
            by_audio.setdefault(entry.audio_url, entry)
            by_loose_audio.setdefault(_loose_url(entry.audio_url), entry)
        if entry.title:
            by_title.setdefault(normalize_title(entry.title), []).append(entry)

    # Exact identifiers for every candidate first, fuzzy titles only for
    # what is left: candidates arrive newest first, and a same-title
    # neighbour whose own entry Apple has not indexed yet must not take
    # the entry that belongs, by GUID, to the episode after it.
    used: set[str] = set()
    found_by_id: Dict[str, Tuple[AppleEpisodeEntry, str]] = {}
    for candidate in candidates:
        exact = _match_exact(candidate, by_guid, by_audio, by_loose_audio, used)
        if exact is not None:
            used.add(exact[0].track_id)
            found_by_id[candidate.episode_id] = exact
    for candidate in candidates:
        if candidate.episode_id in found_by_id:
            continue
        fuzzy = _match_fuzzy(candidate, by_title, used)
        if fuzzy is not None:
            used.add(fuzzy[0].track_id)
            found_by_id[candidate.episode_id] = fuzzy

    matches: List[AppleEpisodeMatch] = []
    for candidate in candidates:
        found = found_by_id.get(candidate.episode_id)
        if found is None:
            continue
        entry, method = found
        matches.append(
            AppleEpisodeMatch(
                episode_id=candidate.episode_id,
                url=entry.track_view_url,
                external_ref=entry.track_id,
                match_method=method,
            )
        )
    return matches


def _match_exact(
    candidate: PlatformLinkCandidate,
    by_guid: Dict[str, AppleEpisodeEntry],
    by_audio: Dict[str, AppleEpisodeEntry],
    by_loose_audio: Dict[str, AppleEpisodeEntry],
    used: set[str],
) -> Optional[Tuple[AppleEpisodeEntry, str]]:
    guid = (candidate.external_id or "").strip()
    entry = by_guid.get(guid) if guid else None
    if entry is not None and entry.track_id not in used:
        return entry, "guid"

    audio = (candidate.audio_url or "").strip()
    if audio:
        entry = by_audio.get(audio) or by_loose_audio.get(_loose_url(audio))
        if entry is not None and entry.track_id not in used:
            return entry, "audio_url"
    return None


def _match_fuzzy(
    candidate: PlatformLinkCandidate,
    by_title: Dict[str, List[AppleEpisodeEntry]],
    used: set[str],
) -> Optional[Tuple[AppleEpisodeEntry, str]]:
    title_key = normalize_title(candidate.title or "")
    if title_key and candidate.pub_date is not None:
        same_title = [e for e in by_title.get(title_key, []) if e.track_id not in used]
        if len(same_title) == 1:
            entry = same_title[0]
            if entry.release_date is not None and _within_tolerance(entry.release_date, candidate.pub_date):
                return entry, "title_date"
    return None


def _within_tolerance(a: datetime, b: datetime) -> bool:
    return abs(_as_utc(a) - _as_utc(b)) <= TITLE_DATE_TOLERANCE


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_title(title: str) -> str:
    """Case, punctuation and whitespace folded — the title equality used by both the matcher and show search."""
    folded = _PUNCT_RE.sub(" ", title.casefold())
    return _WS_RE.sub(" ", folded).strip()


def _loose_url(url: str) -> str:
    """Scheme forced to https, host lower-cased, query and fragment dropped."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def _clean_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _parse_release_date(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)
