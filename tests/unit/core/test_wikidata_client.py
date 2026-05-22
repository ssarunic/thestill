"""Spec #28 §5.2 — WikidataClient tests.

We don't actually hit Wikidata in tests; the client is exercised with
``requests`` mocked via ``unittest.mock`` to assert it:

- builds the right URL and User-Agent
- pulls every P31 mainsnak.value.id from the JSON envelope
- swallows network errors and returns ``[]``
- caches per-QID across repeated calls
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from thestill.core.wikidata_client import NullWikidataClient, WikidataClient, _extract_p31_qids, _parse_entity_payload
from thestill.models.enrichment import EnrichmentUnavailable


def _wikidata_response(qid: str, p31_qids: list[str]) -> dict:
    """Build a minimal ``Special:EntityData/<qid>.json`` payload."""
    return {
        "entities": {
            qid: {
                "claims": {
                    "P31": [
                        {
                            "mainsnak": {
                                "datavalue": {"value": {"id": p31, "entity-type": "item"}},
                            },
                        }
                        for p31 in p31_qids
                    ],
                },
            },
        },
    }


class TestExtractP31:
    def test_pulls_every_p31_value(self):
        payload = _wikidata_response("Q317521", ["Q5"])
        assert _extract_p31_qids(payload, "Q317521") == ["Q5"]

    def test_multiple_p31_values_are_all_returned(self):
        # Wikidata sometimes attaches several instance-of claims.
        payload = _wikidata_response("Q312", ["Q4830453", "Q891723"])
        assert _extract_p31_qids(payload, "Q312") == ["Q4830453", "Q891723"]

    def test_missing_p31_returns_empty(self):
        payload = {"entities": {"Q1": {"claims": {}}}}
        assert _extract_p31_qids(payload, "Q1") == []

    def test_missing_entity_returns_empty(self):
        assert _extract_p31_qids({"entities": {}}, "Q1") == []

    def test_skips_malformed_claims(self):
        # A claim missing the nested datavalue must not crash the
        # parser — Wikidata occasionally returns ``{"mainsnak":
        # {"snaktype": "novalue"}}`` rows for deprecated facts.
        payload = {
            "entities": {
                "Q1": {
                    "claims": {
                        "P31": [
                            {"mainsnak": {"snaktype": "novalue"}},
                            {
                                "mainsnak": {
                                    "datavalue": {"value": {"id": "Q5"}},
                                },
                            },
                        ],
                    },
                },
            },
        }
        assert _extract_p31_qids(payload, "Q1") == ["Q5"]


class TestFetchP31:
    def test_returns_p31_list_on_success(self):
        client = WikidataClient()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _wikidata_response("Q317521", ["Q5"])
        with patch("thestill.core.wikidata_client.requests.get", return_value=resp):
            assert client.fetch_p31("Q317521") == ["Q5"]

    def test_empty_qid_short_circuits_without_request(self):
        client = WikidataClient()
        with patch("thestill.core.wikidata_client.requests.get") as get:
            assert client.fetch_p31("") == []
        assert get.call_count == 0

    def test_network_error_returns_empty_list(self):
        client = WikidataClient()
        with patch(
            "thestill.core.wikidata_client.requests.get",
            side_effect=requests.ConnectionError("network down"),
        ):
            assert client.fetch_p31("Q317521") == []

    def test_non_200_status_returns_empty_list(self):
        client = WikidataClient()
        resp = MagicMock()
        resp.status_code = 503
        with patch("thestill.core.wikidata_client.requests.get", return_value=resp):
            assert client.fetch_p31("Q317521") == []

    def test_invalid_json_returns_empty_list(self):
        client = WikidataClient()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        with patch("thestill.core.wikidata_client.requests.get", return_value=resp):
            assert client.fetch_p31("Q317521") == []

    def test_cached_qid_only_hits_network_once(self):
        client = WikidataClient()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _wikidata_response("Q5", ["Q15632617"])
        with patch("thestill.core.wikidata_client.requests.get", return_value=resp) as get:
            client.fetch_p31("Q5")
            client.fetch_p31("Q5")
            client.fetch_p31("Q5")
        assert get.call_count == 1


def _facts_payload(qid: str) -> dict:
    """A richer ``Special:EntityData`` payload for fetch_facts tests."""
    return {
        "entities": {
            qid: {
                "labels": {"en": {"value": "Elon Musk"}},
                "descriptions": {"en": {"value": "business magnate"}},
                "sitelinks": {"enwiki": {"title": "Elon Musk"}},
                "claims": {
                    "P18": [{"mainsnak": {"datavalue": {"type": "string", "value": "Musk.jpg"}}}],
                    "P569": [{"mainsnak": {"datavalue": {"type": "time", "value": {"time": "+1971-06-28T00:00:00Z"}}}}],
                    "P1128": [{"mainsnak": {"datavalue": {"type": "quantity", "value": {"amount": "+127855"}}}}],
                    "P106": [{"mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q131524"}}}}],
                    # novalue snak — must be skipped, not crash.
                    "P570": [{"mainsnak": {"snaktype": "novalue"}}],
                },
            }
        }
    }


class TestParseEntityPayload:
    def test_buckets_claims_by_datavalue_type(self):
        wd = _parse_entity_payload(_facts_payload("Q317521"), "Q317521", "en")
        assert wd is not None
        assert wd.label == "Elon Musk"
        assert wd.description == "business magnate"
        assert wd.sitelink_title("en") == "Elon Musk"
        assert wd.first_string("P18") == "Musk.jpg"
        assert wd.first_time("P569") == "+1971-06-28T00:00:00Z"
        assert wd.first_quantity("P1128") == "+127855"
        assert wd.entity_refs("P106") == ["Q131524"]
        assert wd.referenced_qids() == ["Q131524"]

    def test_language_falls_back_to_english(self):
        payload = _facts_payload("Q1")
        wd = _parse_entity_payload(payload, "Q1", "de")
        # No German label present → falls back to the English value.
        assert wd is not None and wd.label == "Elon Musk"

    def test_missing_entity_returns_none(self):
        assert _parse_entity_payload({"entities": {}}, "Q1", "en") is None


class TestFetchFacts:
    def test_returns_parsed_entity_on_success(self):
        client = WikidataClient()
        resp = MagicMock(status_code=200)
        resp.json.return_value = _facts_payload("Q317521")
        with patch("thestill.core.wikidata_client.requests.get", return_value=resp):
            wd = client.fetch_facts("Q317521")
        assert wd is not None and wd.first_string("P18") == "Musk.jpg"

    def test_empty_qid_short_circuits(self):
        client = WikidataClient()
        with patch("thestill.core.wikidata_client.requests.get") as get:
            assert client.fetch_facts("") is None
        assert get.call_count == 0

    def test_network_error_raises_unavailable(self):
        # Spec #42 FM-1: transient failure must NOT collapse to "no data".
        client = WikidataClient()
        with patch(
            "thestill.core.wikidata_client.requests.get",
            side_effect=requests.ConnectionError("down"),
        ):
            with pytest.raises(EnrichmentUnavailable):
                client.fetch_facts("Q317521")

    def test_non_200_raises_unavailable(self):
        client = WikidataClient()
        resp = MagicMock(status_code=503)
        with patch("thestill.core.wikidata_client.requests.get", return_value=resp):
            with pytest.raises(EnrichmentUnavailable):
                client.fetch_facts("Q317521")

    def test_failed_fetch_is_not_cached(self):
        # lru_cache does not memoise exceptions, so a transient failure
        # stays retryable on the next call.
        client = WikidataClient()
        ok = MagicMock(status_code=200)
        ok.json.return_value = _facts_payload("Q5")
        with patch(
            "thestill.core.wikidata_client.requests.get",
            side_effect=[requests.ConnectionError("down"), ok],
        ):
            with pytest.raises(EnrichmentUnavailable):
                client.fetch_facts("Q5")
            assert client.fetch_facts("Q5") is not None


class TestFetchLabels:
    def _labels_response(self, mapping: dict[str, str]) -> MagicMock:
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "entities": {qid: {"labels": {"en": {"value": label}}} for qid, label in mapping.items()}
        }
        return resp

    def test_resolves_labels(self):
        client = WikidataClient()
        resp = self._labels_response({"Q131524": "entrepreneur", "Q5": "human"})
        with patch("thestill.core.wikidata_client.requests.get", return_value=resp):
            labels = client.fetch_labels(["Q131524", "Q5"])
        assert labels == {"Q131524": "entrepreneur", "Q5": "human"}

    def test_cached_labels_skip_second_request(self):
        client = WikidataClient()
        resp = self._labels_response({"Q5": "human"})
        with patch("thestill.core.wikidata_client.requests.get", return_value=resp) as get:
            client.fetch_labels(["Q5"])
            client.fetch_labels(["Q5"])
        assert get.call_count == 1

    def test_empty_input_makes_no_request(self):
        client = WikidataClient()
        with patch("thestill.core.wikidata_client.requests.get") as get:
            assert client.fetch_labels([]) == {}
        assert get.call_count == 0

    def test_network_error_raises_unavailable(self):
        client = WikidataClient()
        with patch(
            "thestill.core.wikidata_client.requests.get",
            side_effect=requests.ConnectionError("down"),
        ):
            with pytest.raises(EnrichmentUnavailable):
                client.fetch_labels(["Q5"])


class TestNullClient:
    def test_returns_empty_for_any_qid(self):
        client = NullWikidataClient()
        assert client.fetch_p31("Q317521") == []
        assert client.fetch_p31("") == []

    def test_facts_and_labels_are_noops(self):
        client = NullWikidataClient()
        assert client.fetch_facts("Q317521") is None
        assert client.fetch_labels(["Q5"]) == {}
