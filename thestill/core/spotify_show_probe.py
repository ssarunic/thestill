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
Spotify "latest episode" probe (spec #87 Phase 3).

Spotify's Web API is closed to small apps and the show page serves only
Open Graph tags, but the show's **embed** page still ships its newest
episode inside the page data: Spotify id, the exact title, release date
and duration. One request per show therefore identifies the episode that
was published last, which is the one a listener opens first. Older
episodes are out of reach on this route — see the spec's "Spotify".

The page is served to non-browser user agents only (a browser UA gets the
JavaScript shell), the same quirk the #79 resolver relies on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from structlog import get_logger
from urllib3.util.retry import Retry

from ..utils.url_guard import guarded_session
from ..utils.url_patterns import extract_spotify_entity

logger = get_logger(__name__)

_USER_AGENT = "Thestill/1.0"  # non-browser: gets the server-rendered page
_HTTP_TIMEOUT = 15
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
_EPISODE_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")


@dataclass(frozen=True)
class SpotifyLatestEpisode:
    episode_id: str
    title: str
    released: Optional[datetime]
    duration: Optional[int]  # seconds


def show_id_from_url(spotify_url: Optional[str]) -> Optional[str]:
    """The show id inside a stored ``open.spotify.com/show/<id>`` link, or None."""
    if not spotify_url:
        return None
    entity = extract_spotify_entity(spotify_url)
    return entity[1] if entity and entity[0] == "show" else None


def fetch_latest_episode(show_id: str) -> Optional[SpotifyLatestEpisode]:
    """
    GET the show's embed page and return its newest episode. Raises on
    network / HTTP failures (the service treats that as "retry next pass");
    returns None when the page carries no episode.
    """
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    with guarded_session(user_agent=_USER_AGENT, retries=retry) as session:
        resp = session.get(f"https://open.spotify.com/embed/show/{show_id}", timeout=_HTTP_TIMEOUT)
    if resp.status_code != 200:
        raise requests.HTTPError(f"Spotify embed page returned HTTP {resp.status_code}")
    return parse_embed_page(resp.text)


def parse_embed_page(html: str) -> Optional[SpotifyLatestEpisode]:
    """The newest episode from the embed page's ``__NEXT_DATA__``; None when absent or malformed."""
    match = _NEXT_DATA_RE.search(html or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    entity: Any = data
    for key in ("props", "pageProps", "state", "data", "entity"):
        entity = entity.get(key) if isinstance(entity, dict) else None
        if entity is None:
            return None
    if not isinstance(entity, dict) or entity.get("type") != "episode":
        return None
    episode_id = entity.get("id")
    title = entity.get("title") or entity.get("name")
    if not isinstance(episode_id, str) or not _EPISODE_ID_RE.fullmatch(episode_id):
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    return SpotifyLatestEpisode(
        episode_id=episode_id,
        title=title.strip(),
        released=_parse_release(entity.get("releaseDate")),
        duration=_parse_duration_ms(entity.get("duration")),
    )


def _parse_release(raw: Any) -> Optional[datetime]:
    iso = raw.get("isoString") if isinstance(raw, dict) else raw
    if not isinstance(iso, str) or not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_duration_ms(raw: Any) -> Optional[int]:
    if isinstance(raw, (int, float)) and raw > 0:
        return int(round(raw / 1000))
    return None
