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

"""Spec #69 Phase 6.2 — content-hash ETag responses for write-once resources."""

import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from thestill.web.responses import etag_json_response

app = FastAPI()

PAYLOADS = {
    "a": {"episode_id": "ep-1", "content": "hello world", "nested": {"n": 1}},
    "b": {"episode_id": "ep-1", "content": "changed", "nested": {"n": 1}},
}


@app.get("/resource/{key}")
def resource(key: str, request: Request):
    return etag_json_response(request, PAYLOADS[key])


client = TestClient(app)


def test_response_carries_weak_etag_and_revalidation_headers():
    resp = client.get("/resource/a")
    assert resp.status_code == 200
    # Weak validator: the envelope timestamp keeps bodies from being
    # octet-equal, so a strong tag would be a false claim.
    assert resp.headers["etag"].startswith('W/"') and resp.headers["etag"].endswith('"')
    assert resp.headers["cache-control"] == "private, no-cache"
    body = resp.json()
    # api_response envelope is preserved.
    assert body["status"] == "ok"
    assert "timestamp" in body
    assert body["content"] == "hello world"


def test_etag_is_stable_across_requests_despite_envelope_timestamp():
    first = client.get("/resource/a")
    second = client.get("/resource/a")
    # The bodies genuinely differ (per-request timestamp) while the ETag,
    # which hashes only the payload, stays put. This is exactly why the
    # validator must be weak rather than strong.
    assert first.json()["timestamp"] != second.json()["timestamp"]
    assert first.content != second.content
    assert first.headers["etag"] == second.headers["etag"]


def test_if_none_match_accepts_tag_without_weak_prefix():
    """A client that strips or re-adds ``W/`` still revalidates."""
    etag = client.get("/resource/a").headers["etag"]
    bare = etag.removeprefix("W/")
    assert client.get("/resource/a", headers={"If-None-Match": bare}).status_code == 304


def test_if_none_match_star_revalidates():
    assert client.get("/resource/a", headers={"If-None-Match": "*"}).status_code == 304


def test_if_none_match_returns_304_with_no_body():
    etag = client.get("/resource/a").headers["etag"]
    resp = client.get("/resource/a", headers={"If-None-Match": etag})
    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["etag"] == etag


def test_if_none_match_handles_multiple_candidates():
    etag = client.get("/resource/a").headers["etag"]
    resp = client.get("/resource/a", headers={"If-None-Match": f'"stale-tag", {etag}'})
    assert resp.status_code == 304


def test_changed_payload_changes_etag_and_misses_revalidation():
    etag_a = client.get("/resource/a").headers["etag"]
    resp = client.get("/resource/b", headers={"If-None-Match": etag_a})
    assert resp.status_code == 200
    assert resp.headers["etag"] != etag_a
    assert resp.json()["content"] == "changed"
