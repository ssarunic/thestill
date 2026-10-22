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

"""IndexNow pings for public pages (spec #105 Phase 4).

When a public page appears or disappears (a summary is first written, or an
operator changes a show's visibility), Bing and the other IndexNow engines
are told the URLs at once instead of finding out at the next sitemap crawl.
The key is served at ``/<key>.txt``.

Off unless ``INDEXNOW_KEY`` is set, and only for installs whose public
pages are on. A ping never fails the work that triggered it: errors are
logged and dropped.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable, Iterable, List, Optional
from urllib.parse import urlsplit

import httpx
from structlog import get_logger

if TYPE_CHECKING:
    from ..utils.config import Config

logger = get_logger(__name__)

ENDPOINT = "https://api.indexnow.org/indexnow"
#: IndexNow accepts up to 10,000 URLs per request.
MAX_URLS = 10_000
_TIMEOUT_SECONDS = 10.0


class IndexNow:
    def __init__(self, config: "Config", *, post: Callable[..., Any] = httpx.post) -> None:
        self._key = (config.indexnow_key or "").strip()
        self._base = (config.public_base_url or "").rstrip("/")
        self._on = bool(self._key and self._base and config.multi_user and config.public_pages_enabled)
        self._post = post

    @property
    def enabled(self) -> bool:
        return self._on

    def submit(self, paths: Iterable[str]) -> Optional[int]:
        """POST ``paths`` (site-relative) to IndexNow; the HTTP status, or
        None when off, empty or failed."""
        urls: List[str] = list(dict.fromkeys(f"{self._base}{p}" for p in paths if p.startswith("/")))[:MAX_URLS]
        if not self._on or not urls:
            return None
        try:
            response = self._post(
                ENDPOINT,
                json={
                    "host": urlsplit(self._base).hostname,
                    "key": self._key,
                    "keyLocation": f"{self._base}/{self._key}.txt",
                    "urlList": urls,
                },
                timeout=_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - a ping never fails its caller
            logger.warning("indexnow.failed", urls=len(urls), error=str(exc))
            return None
        status = int(getattr(response, "status_code", 0))
        # 200 and 202 are accepted; 422/403 mean a key or URL problem.
        log = logger.info if status in (200, 202) else logger.warning
        log("indexnow.submitted", urls=len(urls), status=status)
        return status

    def submit_in_background(self, paths: Iterable[str]) -> None:
        """``submit`` on a daemon thread, so a pipeline task never waits on it."""
        if not self._on:
            return
        paths = list(paths)
        threading.Thread(target=self.submit, args=(paths,), name="indexnow", daemon=True).start()
