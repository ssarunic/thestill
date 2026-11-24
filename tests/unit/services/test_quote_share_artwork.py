"""Spec #114 — the artwork download never outlasts its deadline."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from thestill.services import quote_share_service as module
from thestill.services.quote_share_service import QuoteShare, QuoteShareService


def _share(url: str) -> QuoteShare:
    return QuoteShare(
        id="aaaaaaaaaa",
        text="A quote.",
        start_ms=0,
        speaker_name=None,
        speaker_id=None,
        podcast_slug="show",
        podcast_title="Show",
        episode_slug="episode",
        episode_title="Episode",
        image_url=url,
        template_version=1,
    )


@pytest.fixture
def server(monkeypatch):
    """A local image host whose handler the test supplies; loopback is
    allowed for the test only."""
    monkeypatch.setattr(module, "validate_public_url", lambda url: None)
    monkeypatch.setattr(module, "guarded_session", lambda **kwargs: __import__("requests").Session())
    behaviour = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            behaviour["serve"](self)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield behaviour, f"http://127.0.0.1:{httpd.server_address[1]}/art.jpg"
    httpd.shutdown()


def _service() -> QuoteShareService:
    return QuoteShareService(repository=None, config=None)  # type: ignore[arg-type]


def test_artwork_is_returned_when_the_host_answers(server):
    behaviour, url = server

    def serve(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", "5")
        handler.end_headers()
        handler.wfile.write(b"image")

    behaviour["serve"] = serve
    assert _service()._artwork(_share(url)) == b"image"


def test_a_trickling_host_is_abandoned_at_the_deadline(server, monkeypatch):
    behaviour, url = server
    monkeypatch.setattr(module, "_ARTWORK_TOTAL_SECONDS", 0.3)

    def serve(handler):
        handler.send_response(200)
        handler.end_headers()
        try:
            for _ in range(40):  # a byte every 50 ms: never idle long enough for a read timeout
                handler.wfile.write(b"x")
                handler.wfile.flush()
                time.sleep(0.05)
        except OSError:
            pass

    behaviour["serve"] = serve
    started = time.monotonic()
    assert _service()._artwork(_share(url)) is None
    assert time.monotonic() - started < 1.0


def test_an_error_status_or_an_oversized_file_is_no_artwork(server, monkeypatch):
    behaviour, url = server

    def missing(handler):
        handler.send_response(503)
        handler.send_header("Retry-After", "120")
        handler.end_headers()

    behaviour["serve"] = missing
    started = time.monotonic()
    assert _service()._artwork(_share(url)) is None
    assert time.monotonic() - started < 2.0  # no sleeping through Retry-After

    monkeypatch.setattr(module, "_ARTWORK_MAX_BYTES", 10)

    def big(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", "100")
        handler.end_headers()
        handler.wfile.write(b"x" * 100)

    behaviour["serve"] = big
    assert _service()._artwork(_share(url)) is None


def test_a_host_trickling_chunk_framing_does_not_hold_the_caller(server, monkeypatch):
    """Closing a response whose read is blocked can wait for that read; the
    caller must be released at the deadline all the same."""
    behaviour, url = server
    monkeypatch.setattr(module, "_ARTWORK_TOTAL_SECONDS", 0.3)

    def serve(handler):
        handler.protocol_version = "HTTP/1.1"
        handler.send_response(200)
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        try:
            handler.wfile.write(b"5;")  # a chunk-size line that never ends
            for _ in range(60):
                handler.wfile.write(b"x=1;")
                handler.wfile.flush()
                time.sleep(0.05)
        except OSError:
            pass

    behaviour["serve"] = serve
    started = time.monotonic()
    assert _service()._artwork(_share(url)) is None
    assert time.monotonic() - started < 0.8
    # The abandoned download does not linger: its socket was shut, so its
    # thread ends and gives its place in the download budget back.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and any(t.name == "quote-card-artwork" for t in threading.enumerate()):
        time.sleep(0.02)
    assert not any(t.name == "quote-card-artwork" for t in threading.enumerate())
    assert module._artwork_slots.acquire(blocking=False)
    module._artwork_slots.release()


def test_no_download_starts_once_the_budget_is_spent(server, monkeypatch):
    behaviour, url = server
    behaviour["serve"] = lambda handler: pytest.fail("a download started")
    monkeypatch.setattr(module, "_artwork_slots", threading.BoundedSemaphore(1))
    assert module._artwork_slots.acquire(blocking=False)
    assert _service()._artwork(_share(url)) is None


def test_gzipped_artwork_is_decoded(server):
    import gzip

    behaviour, url = server
    packed = gzip.compress(b"image-bytes")

    def serve(handler):
        handler.send_response(200)
        handler.send_header("Content-Encoding", "gzip")
        handler.send_header("Content-Length", str(len(packed)))
        handler.end_headers()
        handler.wfile.write(packed)

    behaviour["serve"] = serve
    assert _service()._artwork(_share(url)) == b"image-bytes"
