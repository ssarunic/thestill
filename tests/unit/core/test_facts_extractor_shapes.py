"""Unit tests for facts-extractor speaker_mapping shape handling.

The structured schema (EpisodeFactsResponse) wants a list of
{speaker_id, name} entries; the legacy JSON-mode path historically
consumed a dict. JSON-mode providers follow the prompt example, so the
example must show the schema shape and the legacy parser must accept both.
"""

from unittest.mock import MagicMock

from thestill.core.facts_extractor import FactsExtractor


def _extractor_with_response(payload: str) -> FactsExtractor:
    provider = MagicMock()
    provider.get_model_name.return_value = "test-model"
    provider.chat_completion.return_value = payload
    return FactsExtractor(provider)


class TestLegacySpeakerMappingShapes:
    def test_list_shape_is_normalised_to_dict(self):
        extractor = _extractor_with_response(
            '{"speaker_mapping": [{"speaker_id": "SPEAKER_00", "name": "Ed Elson (Host)"},'
            ' {"speaker_id": "SPEAKER_01", "name": "Ad Narrator"}],'
            ' "guests": [], "topics_keywords": [], "ad_sponsors": []}'
        )
        facts = extractor._extract_episode_facts_legacy(messages=[], episode_title="Ep")
        assert facts.speaker_mapping == {
            "SPEAKER_00": "Ed Elson (Host)",
            "SPEAKER_01": "Ad Narrator",
        }

    def test_dict_shape_still_accepted(self):
        extractor = _extractor_with_response(
            '{"speaker_mapping": {"SPEAKER_00": "Ed Elson (Host)"},'
            ' "guests": [], "topics_keywords": [], "ad_sponsors": []}'
        )
        facts = extractor._extract_episode_facts_legacy(messages=[], episode_title="Ep")
        assert facts.speaker_mapping == {"SPEAKER_00": "Ed Elson (Host)"}

    def test_malformed_entries_are_skipped(self):
        extractor = _extractor_with_response(
            '{"speaker_mapping": [{"name": "No Id"}, "junk",'
            ' {"speaker_id": "SPEAKER_02", "name": "Guest"}],'
            ' "guests": [], "topics_keywords": [], "ad_sponsors": []}'
        )
        facts = extractor._extract_episode_facts_legacy(messages=[], episode_title="Ep")
        assert facts.speaker_mapping == {"SPEAKER_02": "Guest"}


class TestPromptExampleMatchesSchema:
    def test_example_shows_list_of_entries(self):
        """Regression guard: the JSON example in the extraction prompt must
        match EpisodeFactsResponse (list of entries), because JSON-mode
        providers imitate the example, not the schema."""
        extractor = _extractor_with_response("{}")
        prompt = extractor._build_facts_extraction_system_prompt(language="en")
        assert '"speaker_mapping": [' in prompt
        assert '"speaker_id"' in prompt
