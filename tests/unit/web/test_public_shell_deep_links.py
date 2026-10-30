"""Legacy ``?t=`` / ``?about=`` deep links move to the fragment (public_shell)."""

import pytest

from thestill.web.public_shell import has_legacy_deep_link, move_deep_links_to_fragment


@pytest.mark.parametrize(
    "query, expected",
    [
        ("t=901", ("", "#t=901")),
        ("t=901.25", ("", "#t=901.25")),
        ("view=transcript&t=901", ("?view=transcript", "#t=901")),
        ("about=nvidia&page=2", ("?page=2", "#about=nvidia")),
        ("t=5&about=openai-codex", ("", "#t=5&about=openai-codex")),
        ("t=5&t=9", ("", "#t=5")),
        ("t=soon", ("", "")),
        ("t=-3", ("", "")),
        ("about=Not%20A%20Slug", ("", "")),
        ("about=%3Cscript%3E", ("", "")),
        ("view=summary", ("?view=summary", "")),
        ("", ("", "")),
    ],
)
def test_move_deep_links_to_fragment(query, expected):
    assert move_deep_links_to_fragment(query) == expected


def test_has_legacy_deep_link():
    assert has_legacy_deep_link("t=1")
    assert has_legacy_deep_link("view=transcript&about=x")
    assert has_legacy_deep_link("t=")
    assert not has_legacy_deep_link("view=transcript")
    assert not has_legacy_deep_link("")
    assert not has_legacy_deep_link("tt=1&abouts=x")
