"""``iter_job_audio`` (spec #116): the presigned URL must not reach the logs."""

from __future__ import annotations

import io
import logging
import threading
import urllib.request

import httpx
import pytest

from thestill.core.dalston_transcriber import iter_job_audio

SIGNED = "https://bucket.example/jobs/j1/audio/original.mp3?X-Amz-Signature=SECRETSIGNATURE"
AUDIO = b"abc" * 1000


@pytest.fixture
def dalston(monkeypatch):
    """A Dalston that answers the audio endpoint, and a download that serves bytes."""
    seen = {"api": [], "download": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["api"].append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json={"url": SIGNED, "expires_in": 3600})

    real_client = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    def urlopen(url, timeout=None):
        seen["download"].append(url)
        return io.BytesIO(AUDIO)

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def test_streams_the_audio_and_sends_the_key_only_to_dalston(dalston):
    data = b"".join(iter_job_audio("j1", base_url="http://dalston.test/", api_key="k", chunk_size=512))

    assert data == AUDIO
    assert dalston["api"] == [("http://dalston.test/v1/audio/transcriptions/j1/audio", "Bearer k")]
    assert dalston["download"] == [SIGNED]


def test_presigned_url_is_not_logged_and_log_levels_are_untouched(dalston, caplog):
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)

    with caplog.at_level(logging.DEBUG):
        b"".join(iter_job_audio("j1", base_url="http://dalston.test", api_key="k"))

    assert "SECRETSIGNATURE" not in caplog.text
    assert "dalston.test/v1/audio/transcriptions/j1/audio" in caplog.text  # the unsigned request still logs
    assert logging.getLogger("httpx").level == logging.DEBUG
    assert logging.getLogger("httpcore").level == logging.DEBUG


def test_overlapping_downloads_leave_logging_as_it_was(dalston):
    """Concurrent transcriptions download at the same time; nothing global
    is changed, so there is nothing for them to restore in the wrong order."""
    logging.getLogger("httpx").setLevel(logging.INFO)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(b"".join(iter_job_audio("j1", base_url="http://dalston.test"))))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [AUDIO] * 4
    assert logging.getLogger("httpx").level == logging.INFO


def test_oversized_audio_is_refused(dalston):
    with pytest.raises(ValueError, match="exceeds 100 bytes"):
        b"".join(iter_job_audio("j1", base_url="http://dalston.test", max_bytes=100, chunk_size=64))


def test_non_http_download_url_is_refused(monkeypatch, dalston):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"url": "file:///etc/passwd"})

    monkeypatch.undo()  # drop the fixture's patches, then patch afresh
    plain = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return plain(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    with pytest.raises(ValueError, match="non-HTTP"):
        b"".join(iter_job_audio("j1", base_url="http://dalston.test"))
