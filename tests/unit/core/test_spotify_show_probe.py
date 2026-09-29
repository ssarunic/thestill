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

"""Spec #87 Phase 3 — the show embed page names its newest episode."""

import json
from datetime import datetime, timezone

from thestill.core.spotify_show_probe import parse_embed_page, show_id_from_url


def _page(entity) -> str:
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


LATEST = {
    "type": "episode",
    "id": "082a1V6nazH9ZZqskV1vfz",
    "name": "#502 – Psychiatry, Insane Asylums",
    "title": "#502 – Psychiatry, Insane Asylums",
    "subtitle": "Lex Fridman Podcast",
    "releaseDate": {"isoString": "2026-09-17T00:42:00Z"},
    "duration": 13409643,
}


def test_parses_the_newest_episode():
    latest = parse_embed_page(_page(LATEST))
    assert latest is not None
    assert latest.episode_id == "082a1V6nazH9ZZqskV1vfz"
    assert latest.title == "#502 – Psychiatry, Insane Asylums"
    assert latest.released == datetime(2026, 9, 17, 0, 42, tzinfo=timezone.utc)
    assert latest.duration == 13410  # milliseconds → seconds


def test_tolerates_missing_or_malformed_fields():
    assert parse_embed_page("<html>no data</html>") is None
    assert parse_embed_page('<script id="__NEXT_DATA__">not json</script>') is None
    assert parse_embed_page(_page({"type": "show", "id": "x"})) is None
    assert parse_embed_page(_page({**LATEST, "id": "short"})) is None
    bare = parse_embed_page(_page({"type": "episode", "id": "082a1V6nazH9ZZqskV1vfz", "title": "T"}))
    assert bare is not None and bare.released is None and bare.duration is None


def test_show_id_from_stored_url():
    assert show_id_from_url("https://open.spotify.com/show/2MAi0BvDc6GTFvKFPXnkCL") == "2MAi0BvDc6GTFvKFPXnkCL"
    assert show_id_from_url("https://open.spotify.com/episode/2MAi0BvDc6GTFvKFPXnkCL") is None
    assert show_id_from_url(None) is None and show_id_from_url("") is None
