"""``GET /api/podcasts/{slug}/episodes/{slug}/audio``: where the audio is, over https."""

from __future__ import annotations

from typing import Optional

import pytest

from tests.integration.web.test_api_transcript_words import (
    _build_annotated_transcript,
    _build_raw_transcript,
    _seed_episode,
)
from thestill.web.routes import api_podcasts

EPISODE_ID = "22222222-2222-2222-2222-222222222222"


class StubResolver:
    def __init__(self, answer: Optional[str]) -> None:
        self.answer = answer
        self.asked: list[str] = []
        self.listeners: list[str] = []

    def playable_url(self, audio_url: str, listener: str = "") -> Optional[str]:
        self.asked.append(audio_url)
        self.listeners.append(listener)
        return self.answer


@pytest.fixture
def seeded(app_state, tmp_path):
    return _seed_episode(
        app_state=app_state,
        tmp_path=tmp_path,
        raw_transcript=_build_raw_transcript(),
        annotated_transcript=_build_annotated_transcript(episode_id=EPISODE_ID),
    )


def test_redirects_to_the_resolved_address_and_is_never_cached(client, seeded, monkeypatch):
    podcast, episode = seeded
    stub = StubResolver("https://cdn.example.net/v1/variant/abc.mp3?Signature=s")
    monkeypatch.setattr(api_podcasts, "audio_address_resolver", stub)

    response = client.get(f"/api/podcasts/{podcast}/episodes/{episode}/audio", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://cdn.example.net/v1/variant/abc.mp3?Signature=s"
    assert response.headers["cache-control"] == "no-store"
    # The episode's stored enclosure is what gets resolved.
    assert stub.asked == ["https://example.com/audio.mp3"]
    # …for this listener: the answer is remembered per listener.
    assert len(stub.listeners) == 1 and stub.listeners[0]


def test_is_a_502_when_no_https_address_can_be_reached(client, seeded, monkeypatch):
    podcast, episode = seeded
    monkeypatch.setattr(api_podcasts, "audio_address_resolver", StubResolver(None))

    response = client.get(f"/api/podcasts/{podcast}/episodes/{episode}/audio", follow_redirects=False)

    assert response.status_code == 502


def test_an_unknown_episode_is_not_found_and_nothing_is_resolved(client, seeded, monkeypatch):
    podcast, _ = seeded
    stub = StubResolver("https://cdn.example.net/x.mp3")
    monkeypatch.setattr(api_podcasts, "audio_address_resolver", stub)

    response = client.get(f"/api/podcasts/{podcast}/episodes/no-such-episode/audio", follow_redirects=False)

    assert response.status_code == 404
    assert stub.asked == []
