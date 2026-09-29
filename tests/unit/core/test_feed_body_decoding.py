"""RSS bodies are decoded from bytes, never via ``requests.Response.text``.

``Response.text`` guesses the charset when the server sends none (most
feeds), and a wrong guess double-encodes every accented character —
"Max JungestÃ¥l" reached episode descriptions, facts, transcripts and the
search index that way (2026-09-29). The fetch must honour the XML
declaration and strict UTF-8 instead.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from thestill.core.media_source import RSSMediaSource, decode_feed_body

_UTF8_FEED = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<rss><channel><title>Unsupervised Learning</title>'
    "<item><title>Behind Legora</title><itunes:author>Max Jungestål</itunes:author></item>"
    "</channel></rss>"
)


class TestDecodeFeedBody:
    def test_utf8_body_without_charset_header_stays_utf8(self):
        text, encoding = decode_feed_body(_UTF8_FEED.encode("utf-8"), "application/xml")
        assert "Max Jungestål" in text
        assert encoding == "utf-8"

    def test_text_xml_without_charset_is_not_treated_as_latin1(self):
        # RFC 2616 makes requests default text/* to ISO-8859-1; we must not.
        text, _ = decode_feed_body(_UTF8_FEED.encode("utf-8"), "text/xml")
        assert "JungestÃ¥l" not in text
        assert "Max Jungestål" in text

    def test_xml_declaration_wins_for_a_real_latin1_body(self):
        body = _UTF8_FEED.replace('encoding="UTF-8"', 'encoding="ISO-8859-1"').encode("latin-1")
        text, encoding = decode_feed_body(body, "application/rss+xml")
        assert "Max Jungestål" in text
        assert encoding.lower() == "iso-8859-1"

    def test_header_charset_is_used_when_the_declaration_is_missing(self):
        body = "<rss><channel><title>Jungestål</title></channel></rss>".encode("latin-1")
        text, encoding = decode_feed_body(body, "text/xml; charset=ISO-8859-1")
        assert "Jungestål" in text
        assert encoding == "ISO-8859-1"

    def test_utf8_bom_is_stripped(self):
        text, encoding = decode_feed_body(b"\xef\xbb\xbf" + _UTF8_FEED.encode("utf-8"), None)
        assert text.startswith("<?xml")
        assert encoding == "utf-8-sig"

    def test_undecodable_body_falls_back_without_raising(self):
        text, encoding = decode_feed_body(b'<?xml version="1.0" encoding="bogus-codec"?><a>\xff\xfe</a>', "text/xml")
        assert text.startswith("<?xml")
        assert encoding == "cp1252-replace"


class _Feed(BaseHTTPRequestHandler):
    content_type = "application/xml"  # no charset: the bug's trigger

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        body = _UTF8_FEED.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(params=["application/xml", "text/xml"])
def feed_server(request):
    handler = type("_FeedWithType", (_Feed,), {"content_type": request.param})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://localhost:{server.server_address[1]}/feed.xml"
    server.shutdown()
    thread.join(timeout=5)


def test_fetch_rss_content_keeps_accented_names_intact(monkeypatch, feed_server):
    """End to end through the real session: the served bytes are UTF-8, the
    header carries no charset, and the fetched content must still read
    "Jungestål" — whatever ``Response.text`` would have guessed."""
    monkeypatch.setenv("URL_GUARD_ALLOWLIST", "localhost")

    result = RSSMediaSource().fetch_rss_content(feed_server)

    assert result.status_code == 200
    assert result.content is not None
    assert "Max Jungestål" in result.content
    assert "Ã" not in result.content
