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

"""PostgreSQL implementation of the podcast + episode repository (spec #44).

The SQLite original (``sqlite_podcast_repository.py``, ~4800 lines) implements
both ``PodcastRepository`` and ``EpisodeRepository`` in one class. The port is
split into two focused mixins — podcast-side (incl. refresh scheduling, top
podcasts, categories) and episode-side (incl. pipeline state, transcript
links, imports) — composed here into the single concrete class the factory
hands out. Schema DDL lives in ``postgres_schema.py``; the SQLite class's
migration/seed machinery has no Postgres counterpart by design.
"""

from __future__ import annotations

from structlog import get_logger

from .podcast_repository import EpisodeRepository, PodcastRepository
from .postgres_podcast_repository_episodes import EpisodesMixin
from .postgres_podcast_repository_podcasts import PodcastsMixin

logger = get_logger(__name__)


class PostgresPodcastRepository(PodcastsMixin, EpisodesMixin, PodcastRepository, EpisodeRepository):
    """PostgreSQL-backed podcast + episode repository. Connection-per-op."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres podcast repository")
