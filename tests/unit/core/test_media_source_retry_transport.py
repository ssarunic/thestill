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

"""Spec #60 transport regression — exhausted 5xx must keep its status.

The RSS session's urllib3 ``Retry`` historically raised ``RetryError`` (no
response attached) once its in-request 5xx retries were exhausted, so the
real status was lost and the failure flattened to ``status_code=0``. With
``raise_on_status=False`` the final response is returned and
``raise_for_status`` produces a normal ``HTTPError`` whose status survives
classification. A mocked ``session.get`` cannot reproduce this — the retry
happens inside the adapter — so this test runs a REAL local HTTP server.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from thestill.core.media_source import RSSMediaSource
from thestill.core.refresh_failure import RefreshFailureKind


class _Always503(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture
def http_503_server():
    server = HTTPServer(("127.0.0.1", 0), _Always503)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://localhost:{server.server_address[1]}/feed.xml"
    server.shutdown()
    thread.join(timeout=5)


def test_exhausted_503_preserves_status(monkeypatch, http_503_server):
    # The SSRF guard refuses loopback by default; the allowlist is the
    # supported opt-in for local endpoints (same as a localhost Dalston).
    monkeypatch.setenv("URL_GUARD_ALLOWLIST", "localhost")

    source = RSSMediaSource()
    result = source.fetch_rss_content(http_503_server)

    assert result.status_code == 503  # NOT 0 — the incident's lost bit
    assert result.kind is RefreshFailureKind.REMOTE_TRANSIENT
    assert result.error is not None
    assert result.content is None
