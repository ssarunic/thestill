"""Public page kind classifier (spec #95)."""

import pytest

from thestill.utils.page_kind import SIGNUP_SOURCES, public_page_kind, signup_source_for
from thestill.web import public_shell


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("/", "landing"),
        ("/?ref=x", "landing"),
        ("/top", "top"),
        ("/top/", "top"),
        ("/terms", "legal"),
        ("/privacy#cookies", "legal"),
        ("/podcasts/hard-fork", "podcast"),
        ("/podcasts/hard-fork?intent=follow", "podcast"),
        ("/podcasts/hard-fork/episodes/ep-1", "episode"),
        ("/entities/person/scott-galloway", "entity"),
        ("/entities/topic/ai", "entity"),
        ("/share", "share"),
        ("/share?url=https%3A%2F%2Fx", "share"),
        ("/inbox", None),
        ("/podcasts", None),
        ("/podcasts/x/episodes", None),
        ("/podcasts/x/notes/y", None),
        ("/entities/planet/mars", None),
        ("/entities/person", None),
        ("/top/extra", None),
        ("/podcasts//episodes/x", None),
        ("podcasts/x", None),
        ("", None),
        (None, None),
    ],
)
def test_public_page_kind(path, kind):
    assert public_page_kind(path) == kind


@pytest.mark.parametrize(
    ("next_path", "source"),
    [
        ("/podcasts/x/episodes/y", "episode"),
        ("/", "landing"),
        ("/share?text=hi", "share"),
        ("/terms", "direct"),
        ("/inbox", "direct"),
        (None, "direct"),
    ],
)
def test_signup_source_for(next_path, source):
    assert signup_source_for(next_path) == source
    assert signup_source_for(next_path) in SIGNUP_SOURCES


@pytest.mark.parametrize("garbage", [123, b"/top", object(), "\x00", "/" * 10_000, "/\udcff", ["/top"]])
def test_never_raises(garbage):
    # The OAuth callback turns any exception into a failed login.
    assert public_page_kind(garbage) in (None, "landing")
    assert signup_source_for(garbage) in SIGNUP_SOURCES


def test_matches_the_public_shell_routes():
    # Drift guard: the shell's public static paths and entity types are the
    # ones the classifier labels.
    for path in public_shell._PUBLIC_STATIC_PATHS:  # noqa: SLF001
        assert public_page_kind("/" + path) is not None, path
    for entity_type in public_shell._ENTITY_TYPES:  # noqa: SLF001
        assert public_page_kind(f"/entities/{entity_type}/x") == "entity", entity_type
