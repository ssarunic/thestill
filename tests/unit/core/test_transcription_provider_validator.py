"""
Unit tests for validate_transcription_provider.

Covers module-availability checks for local transcription providers and
required-config checks for cloud providers. Uses monkeypatched find_spec
so the tests don't depend on which optional extras the test venv has.
"""

import importlib.util

import pytest

from thestill.core.transcriber_factory import validate_transcription_provider
from thestill.utils.exceptions import ThestillError


class _Config:
    """Minimal stand-in for thestill.utils.config.Config for tests."""

    def __init__(self, **kwargs):
        self.transcription_provider = kwargs.get("transcription_provider", "")
        self.enable_diarization = kwargs.get("enable_diarization", False)
        self.dalston_base_url = kwargs.get("dalston_base_url", "")
        self.google_app_credentials = kwargs.get("google_app_credentials", "")
        self.google_cloud_project_id = kwargs.get("google_cloud_project_id", "")
        self.elevenlabs_api_key = kwargs.get("elevenlabs_api_key", "")


@pytest.fixture
def force_missing(monkeypatch):
    """Return a helper that makes find_spec return None for specific modules."""

    def _force(*module_names):
        missing = set(module_names)
        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name in missing:
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    return _force


@pytest.fixture
def force_present(monkeypatch):
    """Return a helper that makes find_spec return a truthy value for specific modules."""

    def _force(*module_names):
        present = set(module_names)
        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name in present:
                return object()  # any non-None value suffices
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    return _force


# ---------- Dalston ----------


def test_dalston_requires_base_url():
    config = _Config(transcription_provider="dalston", dalston_base_url="")
    with pytest.raises(ThestillError) as exc:
        validate_transcription_provider(config)
    assert "DALSTON_BASE_URL" in exc.value.message
    assert exc.value.context["provider"] == "dalston"


def test_dalston_accepts_configured_base_url():
    config = _Config(transcription_provider="dalston", dalston_base_url="http://dalston:8080")
    validate_transcription_provider(config)  # should not raise


# ---------- Google ----------


def test_google_requires_credentials():
    config = _Config(transcription_provider="google", google_cloud_project_id="proj")
    with pytest.raises(ThestillError) as exc:
        validate_transcription_provider(config)
    assert "GOOGLE_APP_CREDENTIALS" in exc.value.message


def test_google_requires_project_id():
    config = _Config(transcription_provider="google", google_app_credentials="/path/creds.json")
    with pytest.raises(ThestillError) as exc:
        validate_transcription_provider(config)
    assert "GOOGLE_CLOUD_PROJECT_ID" in exc.value.message


def test_google_accepts_both_fields():
    config = _Config(
        transcription_provider="google",
        google_app_credentials="/path/creds.json",
        google_cloud_project_id="proj",
    )
    validate_transcription_provider(config)


# ---------- ElevenLabs ----------


def test_elevenlabs_requires_api_key():
    config = _Config(transcription_provider="elevenlabs")
    with pytest.raises(ThestillError) as exc:
        validate_transcription_provider(config)
    assert "ELEVENLABS_API_KEY" in exc.value.message


def test_elevenlabs_accepts_api_key():
    config = _Config(transcription_provider="elevenlabs", elevenlabs_api_key="sk-fake")
    validate_transcription_provider(config)


# ---------- Whisper / WhisperX ----------


def test_whisper_missing_modules_raises(force_missing):
    force_missing("torch", "whisper")
    config = _Config(transcription_provider="whisper")
    with pytest.raises(ThestillError) as exc:
        validate_transcription_provider(config)
    assert "torch" in exc.value.message
    assert "whisper" in exc.value.message
    assert "local-transcription" in exc.value.message


def test_whisperx_requires_whisperx_when_diarization_enabled(force_missing, force_present):
    force_present("torch", "whisper")
    force_missing("whisperx")
    config = _Config(transcription_provider="whisper", enable_diarization=True)
    with pytest.raises(ThestillError) as exc:
        validate_transcription_provider(config)
    assert "whisperx" in exc.value.message


def test_whisper_passes_when_modules_present(force_present):
    force_present("torch", "whisper")
    config = _Config(transcription_provider="whisper")
    validate_transcription_provider(config)


def test_empty_provider_falls_through_to_whisper_check(force_missing):
    force_missing("torch", "whisper")
    config = _Config(transcription_provider="")
    with pytest.raises(ThestillError) as exc:
        validate_transcription_provider(config)
    assert "whisper" in exc.value.message


# ---------- Parakeet ----------


def test_parakeet_missing_modules_raises(force_missing):
    force_missing("torch", "transformers", "librosa")
    config = _Config(transcription_provider="parakeet")
    with pytest.raises(ThestillError) as exc:
        validate_transcription_provider(config)
    assert "local-transcription" in exc.value.message


def test_parakeet_passes_when_modules_present(force_present):
    force_present("torch", "transformers", "librosa")
    config = _Config(transcription_provider="parakeet")
    validate_transcription_provider(config)


# ---------- Unknown ----------


def test_unknown_provider_raises():
    config = _Config(transcription_provider="magictranscribe")
    with pytest.raises(ThestillError) as exc:
        validate_transcription_provider(config)
    assert "Unknown TRANSCRIPTION_PROVIDER" in exc.value.message
    assert "magictranscribe" in exc.value.message


# ---------- Case insensitivity ----------


def test_provider_is_case_insensitive():
    config = _Config(transcription_provider="DALSTON", dalston_base_url="http://d:1")
    validate_transcription_provider(config)
