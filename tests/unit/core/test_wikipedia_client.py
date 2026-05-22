"""Spec #45 — WikipediaClient tests.

The client is exercised with ``requests`` mocked via ``unittest.mock``;
we never hit Wikipedia in tests. Asserts it:

- parses the REST ``page/summary`` envelope (extract, url, images)
- treats 404 / disambiguation pages as a genuine miss (``None``)
- raises ``EnrichmentUnavailable`` on a transient failure (FM-1)
- caches per-title across repeated calls
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from thestill.core.wikipedia_client import NullWikipediaClient, WikipediaClient, _parse_summary_payload
from thestill.models.enrichment import EnrichmentUnavailable


def _summary_payload() -> dict:
    return {
        "type": "standard",
        "extract": "Elon Musk is a business magnate.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Elon_Musk"}},
        "thumbnail": {"source": "https://upload.wikimedia.org/thumb/Musk.jpg"},
        "originalimage": {"source": "https://upload.wikimedia.org/Musk.jpg"},
    }


class TestParseSummaryPayload:
    def test_pulls_extract_url_and_images(self):
        summary = _parse_summary_payload(_summary_payload())
        assert summary.extract == "Elon Musk is a business magnate."
        assert summary.url == "https://en.wikipedia.org/wiki/Elon_Musk"
        assert summary.thumbnail_url == "https://upload.wikimedia.org/thumb/Musk.jpg"
        assert summary.original_image_url == "https://upload.wikimedia.org/Musk.jpg"

    def test_tolerates_missing_blocks(self):
        summary = _parse_summary_payload({"extract": "Just prose."})
        assert summary.extract == "Just prose."
        assert summary.url is None
        assert summary.thumbnail_url is None


class TestFetchSummary:
    def test_returns_summary_on_success(self):
        client = WikipediaClient()
        resp = MagicMock(status_code=200)
        resp.json.return_value = _summary_payload()
        with patch("thestill.core.wikipedia_client.requests.get", return_value=resp):
            summary = client.fetch_summary("Elon Musk")
        assert summary is not None and summary.url.endswith("/Elon_Musk")

    def test_empty_title_short_circuits(self):
        client = WikipediaClient()
        with patch("thestill.core.wikipedia_client.requests.get") as get:
            assert client.fetch_summary("") is None
        assert get.call_count == 0

    def test_404_is_a_miss_not_a_failure(self):
        client = WikipediaClient()
        resp = MagicMock(status_code=404)
        with patch("thestill.core.wikipedia_client.requests.get", return_value=resp):
            assert client.fetch_summary("No Such Page") is None

    def test_disambiguation_is_a_miss(self):
        client = WikipediaClient()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"type": "disambiguation", "extract": "May refer to..."}
        with patch("thestill.core.wikipedia_client.requests.get", return_value=resp):
            assert client.fetch_summary("Mercury") is None

    def test_non_200_raises_unavailable(self):
        client = WikipediaClient()
        resp = MagicMock(status_code=503)
        with patch("thestill.core.wikipedia_client.requests.get", return_value=resp):
            with pytest.raises(EnrichmentUnavailable):
                client.fetch_summary("Elon Musk")

    def test_network_error_raises_unavailable(self):
        client = WikipediaClient()
        with patch(
            "thestill.core.wikipedia_client.requests.get",
            side_effect=requests.ConnectionError("down"),
        ):
            with pytest.raises(EnrichmentUnavailable):
                client.fetch_summary("Elon Musk")

    def test_cached_title_only_hits_network_once(self):
        client = WikipediaClient()
        resp = MagicMock(status_code=200)
        resp.json.return_value = _summary_payload()
        with patch("thestill.core.wikipedia_client.requests.get", return_value=resp) as get:
            client.fetch_summary("Elon Musk")
            client.fetch_summary("Elon Musk")
        assert get.call_count == 1


class TestNullClient:
    def test_always_misses(self):
        assert NullWikipediaClient().fetch_summary("Elon Musk") is None
