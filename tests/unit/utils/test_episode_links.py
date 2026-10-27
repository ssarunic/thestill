"""``episode_links`` — the one builder for episode page + audio links."""

from thestill.utils.url_generator import episode_links


def _links(**overrides):
    args = dict(
        base_url="https://thestill.me",
        podcast_slug="prof-g",
        episode_slug="capex",
        audio_url="https://cdn.example.com/a.mp3",
    )
    args.update(overrides)
    return episode_links(**args)


def test_page_without_a_moment_has_no_fragment():
    assert _links() == {
        "web_url": "/podcasts/prof-g/episodes/capex",
        "episode_url": "https://thestill.me/podcasts/prof-g/episodes/capex",
        "audio_url": "https://cdn.example.com/a.mp3",
    }


def test_moment_is_a_whole_second_fragment():
    # #t=, not ?t=: one URL per episode for crawlers, and it seeks signed-out.
    assert _links(start_ms=61_999)["episode_url"].endswith("/episodes/capex#t=61")


def test_trailing_slash_on_the_base_is_not_doubled():
    assert (
        _links(base_url="https://thestill.me/")["episode_url"] == "https://thestill.me/podcasts/prof-g/episodes/capex"
    )


def test_no_base_url_keeps_the_path_but_no_absolute_link():
    links = _links(base_url="")
    assert links["web_url"] == "/podcasts/prof-g/episodes/capex"
    assert links["episode_url"] is None


def test_missing_slug_gives_no_page_link():
    assert _links(episode_slug="")["web_url"] is None
    assert _links(podcast_slug=None)["episode_url"] is None


def test_empty_audio_url_is_none():
    assert _links(audio_url="")["audio_url"] is None
