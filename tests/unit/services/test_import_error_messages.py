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

"""Spec #112: import error text never embeds exception text.

``ImportError`` / ``SpotifyResolutionError`` messages reach web and MCP
clients verbatim, so a driver, HTTP-client or parser message (hostnames,
ports, internals) must stay on ``__cause__`` and in the log.
"""

import pytest
import requests

from thestill.core import spotify_resolver
from thestill.core.spotify_resolver import SpotifyResolutionError
from thestill.services import import_service
from thestill.services.import_service import ResolverError

SECRET = "internal-db.local:5432 connection refused"


class _Session:
    def __init__(self, *, raise_on_get=None, response=None):
        self._raise = raise_on_get
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        if self._raise is not None:
            raise self._raise
        return self._response

    post = get


class _NonJson:
    status_code = 200

    def json(self):
        raise ValueError(f"Expecting value: line 1 column 1 ({SECRET})")


def _patch_session(monkeypatch, module, session):
    monkeypatch.setattr(module, "guarded_session", lambda **kwargs: session)


def _assert_clean(exc_info, cause_type):
    assert SECRET not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, cause_type)


class TestImportService:
    def test_fetch_failure_keeps_exception_text_out_of_message(self, monkeypatch):
        _patch_session(monkeypatch, import_service, _Session(raise_on_get=requests.ConnectionError(SECRET)))
        with pytest.raises(ResolverError, match="fetch failed") as exc_info:
            import_service._guarded_get("https://itunes.apple.com/lookup?id=1", label="iTunes lookup")
        _assert_clean(exc_info, requests.ConnectionError)

    def test_non_json_lookup_keeps_parser_text_out_of_message(self, monkeypatch):
        _patch_session(monkeypatch, import_service, _Session(response=_NonJson()))
        with pytest.raises(ResolverError, match="non-JSON") as exc_info:
            import_service._itunes_lookup("id=1", label="trackId 1")
        _assert_clean(exc_info, ValueError)


class TestSpotifyResolver:
    def test_fetch_failure_keeps_exception_text_out_of_message(self, monkeypatch):
        _patch_session(monkeypatch, spotify_resolver, _Session(raise_on_get=requests.ConnectionError(SECRET)))
        with pytest.raises(SpotifyResolutionError, match="fetch failed") as exc_info:
            spotify_resolver._guarded_get("https://open.spotify.com/episode/x", label="Spotify page")
        _assert_clean(exc_info, requests.ConnectionError)

    def test_non_json_itunes_keeps_parser_text_out_of_message(self, monkeypatch):
        _patch_session(monkeypatch, spotify_resolver, _Session(response=_NonJson()))
        with pytest.raises(SpotifyResolutionError, match="non-JSON") as exc_info:
            spotify_resolver.itunes_lookup("id=1")
        _assert_clean(exc_info, ValueError)
