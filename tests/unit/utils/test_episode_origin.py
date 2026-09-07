"""Spec #76 §3.6 — canonical_id prefix → (origin, import_kind)."""

import pytest

from thestill.utils.episode_origin import derive_episode_origin


@pytest.mark.parametrize(
    ("canonical_id", "expected"),
    [
        (None, ("feed", None)),
        ("", ("feed", None)),
        ("audio:" + "a" * 64, ("import", "bare_audio")),
        ("youtube:dQw4w9WgXcQ", ("import", "youtube")),
        ("apple:1000123", ("import", "apple_episode")),
        ("rss:https://example.com/feed#ep-1", ("import", "rss_episode")),
        # Unknown resolver: still an import, kind unlabelled, never raises.
        ("future:xyz", ("import", None)),
        ("no-separator", ("import", None)),
    ],
)
def test_derive_episode_origin(canonical_id, expected):
    assert derive_episode_origin(canonical_id) == expected
