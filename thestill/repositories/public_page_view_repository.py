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

"""Abstract repository for anonymous public page views (spec #103 Phase 2).

A per-day counter per public page, keyed by object id (a renamed slug keeps
its history). No visitor id, IP, user agent or referrer: a daily count per
page cannot single anyone out.
"""

from abc import ABC, abstractmethod
from datetime import date


class PublicPageViewRepository(ABC):
    """Abstract repository for ``public_page_daily_views``."""

    @abstractmethod
    def increment(self, day: date, kind: str, object_id: str) -> None:
        """Count one view of the ``kind`` page for ``object_id`` (``''`` for
        pages with no object, such as the landing page) on UTC ``day``."""
