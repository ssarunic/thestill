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

"""Admin edits to a show's settings, audited.

The CLI (``set-show-link``, ``set-summary-profile``,
``set-podcast-visibility``) and any admin API share this service. Each write
records its audit row here, so no entry point can skip it. The sibling
``EntityCurationService`` does the same for entities, mentions and roles.
"""

from __future__ import annotations

from typing import Optional

from ..core.summary_profiles import PROFILES
from ..models.podcast import Podcast
from ..repositories.podcast_repository import PodcastRepository
from .abuse.actor import Actor
from .abuse.audit_service import AuditLog
from .abuse.errors import AdminActionError

SHOW_LINK_PLATFORMS = ("apple", "youtube", "spotify")

# A curated link must look like the platform's show page.
_SHOW_LINK_MARKERS = {"spotify": "spotify.com/show/", "youtube": "youtube.com/"}


class PodcastCurationService:
    def __init__(self, podcasts: PodcastRepository, audit: AuditLog) -> None:
        self._repo = podcasts
        self._audit = audit

    def _get(self, podcast_id: str) -> Podcast:
        podcast = self._repo.get_by_id(podcast_id)
        if podcast is None:
            raise AdminActionError("not_found", f"No podcast {podcast_id!r}.")
        return podcast

    def set_show_link(self, podcast_id: str, platform: str, url: Optional[str], *, actor: Actor) -> Podcast:
        """Set (or, with ``url=None``, clear) the curated Apple / YouTube /
        Spotify link. A curated link survives chart syncs and resolvers."""
        if platform not in SHOW_LINK_PLATFORMS:
            raise AdminActionError("invalid_platform", f"Platform must be one of {', '.join(SHOW_LINK_PLATFORMS)}.")
        marker = _SHOW_LINK_MARKERS.get(platform)
        if url and marker and marker not in url:
            raise AdminActionError("invalid_url", f"That does not look like a {platform} show link: {url}")
        podcast = self._get(podcast_id)
        old = getattr(podcast, f"{platform}_url", None)
        self._repo.set_podcast_platform_url(str(podcast.id), platform, url, source="curated")
        self._audit.record(
            actor, "podcast.set_show_link", podcast_id=str(podcast.id), platform=platform, old=old, new=url
        )
        return podcast

    def set_summary_profile(self, podcast_id: str, profile: Optional[str], *, actor: Actor) -> Podcast:
        """Pin the summary genre profile, or ``None`` to resolve it from the
        show's categories (spec #98)."""
        if profile is not None and profile not in PROFILES:
            raise AdminActionError(
                "invalid_profile", f"Unknown profile {profile!r}. Choose one of: {', '.join(sorted(PROFILES))}."
            )
        podcast = self._get(podcast_id)
        old = podcast.summary_profile
        self._repo.set_podcast_summary_profile(str(podcast.id), profile)
        podcast.summary_profile = profile
        self._audit.record(actor, "podcast.set_summary_profile", podcast_id=str(podcast.id), old=old, new=profile)
        return podcast

    def set_public_pages(self, podcast_id: str, public: bool, *, actor: Actor, reason: Optional[str] = None) -> Podcast:
        """The publisher takedown switch for signed-out pages (spec #94)."""
        podcast = self._get(podcast_id)
        self._repo.set_podcast_public_pages(str(podcast.id), public)
        self._audit.record(actor, "podcast.set_public_pages", podcast_id=str(podcast.id), public=public, reason=reason)
        return podcast
