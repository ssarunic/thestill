"""Artwork URL HTTPS upgrade applied during RSS ingestion.

The web UI's CSP only permits ``img-src https:``, so HTTP-only artwork
(e.g. BBC's ``ichef.bbci.co.uk``) must be rewritten at extraction time.
"""

import pytest

from thestill.core.media_source import _upgrade_image_url_to_https


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (
            "http://ichef.bbci.co.uk/images/ic/3000x3000/p0abc.jpg",
            "https://ichef.bbci.co.uk/images/ic/3000x3000/p0abc.jpg",
        ),
        ("https://cdn.example.com/art.png", "https://cdn.example.com/art.png"),
        ("HTTP://upper.example.com/art.png", "HTTP://upper.example.com/art.png"),  # scheme match is exact
        ("//protocol-relative.example.com/art.png", "//protocol-relative.example.com/art.png"),
        ("", ""),
        (None, None),
    ],
)
def test_upgrade_image_url_to_https(given, expected):
    assert _upgrade_image_url_to_https(given) == expected
