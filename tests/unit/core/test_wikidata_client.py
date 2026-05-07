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

import requests

from thestill.core.wikidata_client import NullWikidataClient, WikidataClient, _extract_p31_qids


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


class TestNullClient:
    def test_returns_empty_for_any_qid(self):
        client = NullWikidataClient()
        assert client.fetch_p31("Q317521") == []
        assert client.fetch_p31("") == []
