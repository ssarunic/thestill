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
Match feed episodes to a show's YouTube channel uploads (spec #87, Phase 2).

YouTube has no GUID and channels post clips and shorts beside full episodes,
so this matcher is deliberately stricter than the Apple one:

- ``title_date``: normalised titles equal and the upload date within
  tolerance of ``pub_date``; when both durations are known they must also
  agree within :func:`duration_tolerance`.
- ``title_duration``: the date within tolerance, the durations known and
  agreeing, AND the titles related in one of two explainable ways — one
  normalised title contains the other (feeds prefix "Ep 12:" or suffix
  "| Show Name"; the shorter side at least :data:`MIN_CONTAINED_TITLE`
  characters), or both carry the same single ``#NNN`` episode number
  ("#502 – Guest: Topic" vs "Topic | Show #502"). Without a duration
  neither is enough: a clip named after the episode would pass.

**Dates are approximate.** A flat listing only carries the page's
"3 weeks ago" / "2 months ago" text, so the further back an episode is
the coarser its upload date: :func:`date_tolerance` widens with age
(3 days inside three weeks, two weeks inside eight weeks, 45 days
beyond). The exact duration carries the precision the date loses.

A candidate with more than one qualifying upload is skipped, and each
upload links at most one episode. The listing itself
(:func:`list_channel_videos`) is a flat yt-dlp pass over the channel's
``/videos`` tab — no shorts, no downloads, one round trip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from structlog import get_logger

from ..models.podcast import PlatformLinkCandidate
from ..utils.url_patterns import YOUTUBE_VIDEO_ID_RE

logger = get_logger(__name__)

# Approximate upload dates (see module docstring): the page shows "N days
# ago" for about three weeks, "N weeks ago" to about eight weeks, then
# "N months ago" (a 75-day-old upload was listed as "3 months ago"). Each
# band gets a tolerance a little wider than its unit.
DATE_TOLERANCE_RECENT = timedelta(days=3)  # "audio Monday, video Wednesday"
DATE_TOLERANCE_WEEKS = timedelta(days=14)
DATE_TOLERANCE_MONTHS = timedelta(days=45)
RECENT_AGE = timedelta(days=21)
WEEKS_AGE = timedelta(days=56)
# Full episodes on YouTube carry the same audio plus intro/outro cards.
DURATION_TOLERANCE_FLOOR = 180  # seconds
DURATION_TOLERANCE_RATIO = 0.20
MIN_CONTAINED_TITLE = 12  # normalised characters before containment counts ("2519 scott eastwood")

# Newest uploads to list. Channels that post daily clips need a deep window
# for the episodes inside it to be reachable at all.
DEFAULT_LISTING_LIMIT = 300

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
# "#502", "#2519" — the episode number publishers put in both titles.
_EPISODE_NUMBER_RE = re.compile(r"#(\d{1,6})(?!\d)")


@dataclass(frozen=True)
class YouTubeVideoEntry:
    """One upload as the flat listing reports it."""

    video_id: str
    title: str
    uploaded: Optional[datetime]  # approximate (day granularity)
    duration: Optional[int]  # seconds


@dataclass(frozen=True)
class YouTubeEpisodeMatch:
    episode_id: str
    url: str
    external_ref: str
    match_method: str  # 'title_date' | 'title_duration'


def channel_videos_url(channel_url: str) -> str:
    """The channel's ``/videos`` tab (uploads only, no shorts or live tab)."""
    parts = urlsplit(channel_url.strip())
    path = parts.path.rstrip("/")
    if path.endswith(("/videos", "/streams", "/podcasts")) or "/playlist" in path:
        return urlunsplit((parts.scheme or "https", parts.netloc, path, parts.query, ""))
    return urlunsplit((parts.scheme or "https", parts.netloc, path + "/videos", "", ""))


def list_channel_videos(channel_url: str, *, limit: int = DEFAULT_LISTING_LIMIT) -> List[YouTubeVideoEntry]:
    """
    Flat yt-dlp listing of the channel's newest ``limit`` uploads.

    Raises whatever yt-dlp raises (network, bot check, private channel) —
    the service treats any error as "lookup failed, retry next refresh".
    """
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": int(limit),
        # Flat entries carry no upload_date by default; this asks the tab
        # extractor for the approximate timestamp shown on the page.
        "extractor_args": {"youtubetab": {"approximate_date": ["true"]}},
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(channel_videos_url(channel_url), download=False)
    return parse_listing((info or {}).get("entries") or [])


def fetch_video(video_id: str) -> Optional[YouTubeVideoEntry]:
    """
    One video's title, exact upload date and duration (yt-dlp, no download).
    Used to verify a description link when the video is not in the channel
    listing. Raises on network / bot-check failures; returns None when the
    response is unusable.
    """
    import yt_dlp

    options = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    parsed = parse_listing([info] if isinstance(info, dict) else [])
    return parsed[0] if parsed else None


def parse_listing(entries: Iterable[Any]) -> List[YouTubeVideoEntry]:
    """Keep well-formed uploads; the listing is untrusted input."""
    parsed: List[YouTubeVideoEntry] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        video_id = raw.get("id")
        title = raw.get("title")
        if not isinstance(video_id, str) or not YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
            continue
        if not isinstance(title, str) or not title.strip():
            continue
        if raw.get("live_status") in ("is_live", "is_upcoming"):
            continue
        parsed.append(
            YouTubeVideoEntry(
                video_id=video_id,
                title=title.strip(),
                uploaded=_parse_uploaded(raw.get("timestamp"), raw.get("upload_date")),
                duration=_parse_duration(raw.get("duration")),
            )
        )
    return parsed


def match_candidates(
    candidates: Sequence[PlatformLinkCandidate],
    entries: Sequence[YouTubeVideoEntry],
    *,
    now: Optional[datetime] = None,
) -> List[YouTubeEpisodeMatch]:
    """``now`` anchors the age-dependent date tolerance (defaults to the wall clock)."""
    at = now or datetime.now(timezone.utc)
    normalized: List[_Entry] = [_Entry(e, normalize_title(e.title), episode_number(e.title)) for e in entries]
    # Equal titles for every candidate first, contained titles / shared
    # numbers only for what is left, so a looser neighbour cannot consume
    # the upload that is another episode's exact match.
    used: set[str] = set()
    found_by_id: Dict[str, Tuple[YouTubeVideoEntry, str]] = {}
    for allowed in (("title_date",), ("title_duration",)):
        for candidate in candidates:
            if candidate.episode_id in found_by_id:
                continue
            found = _match_one(candidate, normalized, used, at, allowed)
            if found is None:
                continue
            used.add(found[0].video_id)
            found_by_id[candidate.episode_id] = found
    matches: List[YouTubeEpisodeMatch] = []
    for candidate in candidates:
        found = found_by_id.get(candidate.episode_id)
        if found is None:
            continue
        entry, method = found
        matches.append(
            YouTubeEpisodeMatch(
                episode_id=candidate.episode_id,
                url=f"https://www.youtube.com/watch?v={entry.video_id}",
                external_ref=entry.video_id,
                match_method=method,
            )
        )
    return matches


def match_rule(
    candidate: PlatformLinkCandidate,
    *,
    title: str,
    released: Optional[datetime],
    duration: Optional[int],
    now: Optional[datetime] = None,
) -> Optional[str]:
    """
    Which rule (``title_date`` / ``title_duration``), if any, says the item
    described by ``title`` / ``released`` / ``duration`` IS ``candidate``.

    The single-item form of the matcher, shared with the verification of a
    publisher's description link (spec #87 "publisher"): a link in the show
    notes may point at last week's episode, so the linked item's own
    metadata has to pass the same test an upload from the channel would.
    """
    if candidate.pub_date is None or released is None:
        return None
    at = now or datetime.now(timezone.utc)
    item = _Entry(YouTubeVideoEntry("", title, released, duration), normalize_title(title), episode_number(title))
    found = _match_one(candidate, [item], set(), at, ("title_date", "title_duration"))
    return found[1] if found else None


@dataclass(frozen=True)
class _Entry:
    entry: YouTubeVideoEntry
    title: str  # normalised
    number: Optional[str]


def claim_corroborated(
    candidate: PlatformLinkCandidate,
    *,
    released: Optional[datetime],
    duration: Optional[int],
    now: Optional[datetime] = None,
) -> bool:
    """
    Weaker test for an item the PUBLISHER linked (spec #87 "publisher"):
    the date within tolerance AND the durations known and agreeing. The
    publisher's own link plus a matching length and day is enough to
    accept a video retitled for YouTube ("Nick Lane – Life as we know it"
    became "I find it almost disturbing that the universe favors life");
    a bite-size cut or a trailer still fails on length. Never used for
    channel uploads, where nobody vouched for the pairing.
    """
    if candidate.pub_date is None or released is None:
        return False
    at = now or datetime.now(timezone.utc)
    if not _dates_close(released, candidate.pub_date, date_tolerance(candidate.pub_date, at)):
        return False
    return _durations_agree(duration, candidate.duration) is True


def _match_one(
    candidate: PlatformLinkCandidate,
    entries: Sequence[_Entry],
    used: set[str],
    now: datetime,
    allowed: Tuple[str, ...],
) -> Optional[Tuple[YouTubeVideoEntry, str]]:
    title = normalize_title(candidate.title or "")
    if not title or candidate.pub_date is None:
        return None
    number = episode_number(candidate.title or "")
    tolerance = date_tolerance(candidate.pub_date, now)
    qualifying: List[Tuple[YouTubeVideoEntry, str]] = []
    for item in entries:
        entry = item.entry
        if entry.video_id in used or entry.uploaded is None:
            continue
        if not _dates_close(entry.uploaded, candidate.pub_date, tolerance):
            continue
        durations_agree = _durations_agree(entry.duration, candidate.duration)
        if item.title == title:
            if durations_agree is False:
                continue  # same title, clearly different length: a clip or a re-cut
            if "title_date" in allowed:
                qualifying.append((entry, "title_date"))
        elif (
            "title_duration" in allowed
            and durations_agree is True
            and (_contains(item.title, title) or (number is not None and item.number == number))
        ):
            qualifying.append((entry, "title_duration"))
    if len(qualifying) != 1:
        return None
    return qualifying[0]


def episode_number(title: str) -> Optional[str]:
    """The single ``#NNN`` in a title, or None when there is none or several."""
    numbers = {m.group(1).lstrip("0") or "0" for m in _EPISODE_NUMBER_RE.finditer(title or "")}
    return next(iter(numbers)) if len(numbers) == 1 else None


def date_tolerance(pub_date: datetime, now: datetime) -> timedelta:
    """How far an approximate upload date may sit from ``pub_date`` — wider with age."""
    age = _as_utc(now) - _as_utc(pub_date)
    if age <= RECENT_AGE:
        return DATE_TOLERANCE_RECENT
    if age <= WEEKS_AGE:
        return DATE_TOLERANCE_WEEKS
    return DATE_TOLERANCE_MONTHS


def _contains(a: str, b: str) -> bool:
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= MIN_CONTAINED_TITLE and shorter in longer


def _durations_agree(video: Optional[int], episode: Optional[int]) -> Optional[bool]:
    """True/False when both are known, None when either is missing."""
    if not video or not episode:
        return None
    tolerance = max(DURATION_TOLERANCE_FLOOR, int(episode * DURATION_TOLERANCE_RATIO))
    return abs(int(video) - int(episode)) <= tolerance


def _dates_close(uploaded: datetime, pub_date: datetime, tolerance: timedelta) -> bool:
    return abs(_as_utc(uploaded).date() - _as_utc(pub_date).date()) <= tolerance


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_title(title: str) -> str:
    folded = _PUNCT_RE.sub(" ", title.casefold())
    return _WS_RE.sub(" ", folded).strip()


def _parse_uploaded(timestamp: Any, upload_date: Any) -> Optional[datetime]:
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(upload_date, str) and len(upload_date) == 8 and upload_date.isdigit():
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _parse_duration(raw: Any) -> Optional[int]:
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    return None


def describe(entries: Sequence[YouTubeVideoEntry]) -> Dict[str, int]:
    """Listing shape for log lines: how many uploads carried a date / duration."""
    return {
        "entries": len(entries),
        "with_date": sum(1 for e in entries if e.uploaded is not None),
        "with_duration": sum(1 for e in entries if e.duration is not None),
    }
