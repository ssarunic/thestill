"""``iter_job_audio`` (spec #116): the presigned URL must not reach the logs."""

from __future__ import annotations

import logging

import httpx

from thestill.core import dalston_transcriber
from thestill.core.dalston_transcriber import iter_job_audio

SIGNED = "https://bucket.example/jobs/j1/audio/original.mp3?X-Amz-Signature=SECRETSIGNATURE"


def _client_factory(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization")))
        if request.url.host == "dalston.test":
            return httpx.Response(200, json={"url": SIGNED, "expires_in": 3600})
        return httpx.Response(200, content=b"abc" * 1000)

    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    return factory


def test_streams_the_audio_and_sends_the_key_only_to_dalston(monkeypatch):
    seen = []
    monkeypatch.setattr(httpx, "Client", _client_factory(seen))

    data = b"".join(iter_job_audio("j1", base_url="http://dalston.test/", api_key="k"))

    assert data == b"abc" * 1000
    assert seen[0] == ("http://dalston.test/v1/audio/transcriptions/j1/audio", "Bearer k")
    assert seen[1] == (SIGNED, None)


def test_presigned_url_is_not_logged(monkeypatch, caplog):
    monkeypatch.setattr(httpx, "Client", _client_factory([]))
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)

    with caplog.at_level(logging.DEBUG):
        b"".join(iter_job_audio("j1", base_url="http://dalston.test", api_key="k"))

    assert "SECRETSIGNATURE" not in caplog.text
    assert "dalston.test/v1/audio/transcriptions/j1/audio" in caplog.text  # the unsigned request still logs
    # and the loggers are put back afterwards
    assert logging.getLogger("httpx").level == logging.DEBUG


def test_oversized_audio_is_refused(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _client_factory([]))
    try:
        b"".join(iter_job_audio("j1", base_url="http://dalston.test", max_bytes=100, chunk_size=64))
    except ValueError as exc:
        assert "exceeds 100 bytes" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
    assert dalston_transcriber.DEFAULT_BASE_URL  # module default still present
