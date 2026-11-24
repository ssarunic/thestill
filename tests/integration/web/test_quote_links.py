"""Quote short links (spec #114 Phase 0): minting, ``/q/<id>``, the card, takedown."""

import io
import urllib.robotparser

import pytest
from PIL import Image

from thestill.repositories.factory import make_repositories
from thestill.services.abuse.actor import Actor
from thestill.services.quote_share_service import QuoteShareService, share_id

from .test_public_api import (  # noqa: F401 (fixtures)
    ELSON_LINE,
    GALLOWAY_LINE,
    _fresh_buckets,
    _seed_people,
    anon,
    app_config,
)

EPISODE_ID = "00000000-0000-0000-0000-000000000001"
EPISODE_QUOTES = "/api/public/podcasts/public-show/episodes/summarised/quotes"
CHROME = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"}


@pytest.fixture
def shares(app_config, app_state) -> QuoteShareService:
    service = QuoteShareService(
        make_repositories(app_config).quote_share,
        app_config,
        settings=app_state.abuse.settings,
        audit=app_state.abuse.audit,
    )
    app_state.quote_share_service = service
    _seed_people(app_state)
    return service


def _quotes(anon) -> dict:
    response = anon.get(EPISODE_QUOTES)
    assert response.status_code == 200, response.text
    return {quote["text"]: quote for quote in response.json()["quotes"]}


def test_public_quotes_carry_a_share_id_backed_by_a_row(anon, shares):
    quotes = _quotes(anon)
    galloway = quotes[GALLOWAY_LINE]
    # Deterministic: the episode, the quote's start and its words.
    assert galloway["share_id"] == share_id(EPISODE_ID, 2 * 60_000 + 5_000, GALLOWAY_LINE)
    assert quotes[ELSON_LINE]["share_id"] != galloway["share_id"]
    rows = shares.list_for_episode(EPISODE_ID)
    assert {row["id"] for row in rows} == {quote["share_id"] for quote in quotes.values()}
    # The source turn's end is stored with the start: the speaker's anchor.
    stored = make_repositories(anon.app.state.app_state.config).quote_share.get(galloway["share_id"])
    assert (stored["start_ms"], stored["end_ms"]) == (125_000, 140_000)


def test_minting_is_idempotent_and_the_same_words_share_one_link(anon, shares, app_state):
    first = _quotes(anon)
    from thestill.web.public_access import clear_public_caches

    clear_public_caches()
    assert _quotes(anon) == first
    assert len(shares.list_for_episode(EPISODE_ID)) == len(first)
    # The same quote reached from an entity page has the same link.
    entity = anon.get("/api/public/entities/person/scott-galloway").json()
    assert entity["featured_quote"]["share_id"] == first[GALLOWAY_LINE]["share_id"]


def test_read_only_mints_nothing(anon, shares, app_state):
    app_state.abuse.settings.set("read_only", True, actor=Actor.cli())
    quotes = _quotes(anon)
    assert all(quote["share_id"] is None for quote in quotes.values())
    assert shares.list_for_episode(EPISODE_ID) == []


def test_a_quote_that_does_not_stand_alone_gets_no_link(shares):
    rows = [
        {"mention_id": 1, "episode_id": EPISODE_ID, "podcast_id": "p", "segment_id": 1, "start_ms": 1, "quote": text}
        for text in ("and that is why it matters to everyone here.", "It trails off without an ending", "A" * 330 + ".")
    ]
    assert shares.mint(rows) == {}


def test_short_link_page_is_the_card_for_every_user_agent(anon, shares):
    sid = _quotes(anon)[GALLOWAY_LINE]["share_id"]
    bodies = []
    for agent in (CHROME["user-agent"], "Twitterbot/1.0", "curl/8.4.0"):
        response = anon.get(f"/q/{sid}", headers={"user-agent": agent})
        assert response.status_code == 200, response.text
        bodies.append(response.text)
    page = bodies[0]
    assert bodies[1] == page and bodies[2] == page
    # D1: og:url is the short link, the canonical is the episode.
    assert f'<meta property="og:url" content="https://thestill.test/q/{sid}">' in page
    assert f'<meta property="og:image" content="https://thestill.test/q/{sid}.png">' in page
    assert '<link rel="canonical" href="https://thestill.test/podcasts/public-show/episodes/summarised">' in page
    assert '<meta name="robots" content="noindex">' in page
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    # The landing: the episode at that moment, with the share id in the fragment.
    landing = f"https://thestill.test/podcasts/public-show/episodes/summarised#t=125&amp;s={sid}"
    assert f'<meta http-equiv="refresh" content="0;url={landing}">' in page
    assert f'<a href="{landing}"' in page
    assert "Scott Galloway on " in page
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex"


def test_opens_are_counted_for_browsers_only(anon, shares):
    sid = _quotes(anon)[GALLOWAY_LINE]["share_id"]
    anon.get(f"/q/{sid}", headers={**CHROME, "referer": "https://www.linkedin.com/feed/"})
    anon.get(f"/q/{sid}", headers=CHROME)
    anon.get(f"/q/{sid}", headers={"user-agent": "Twitterbot/1.0"})
    anon.get(f"/q/{sid}", headers={"user-agent": "Googlebot/2.1"})
    opens = {row["id"]: row["opens"] for row in shares.list_for_episode(EPISODE_ID)}
    assert opens[sid] == 2


def test_unknown_ids_are_a_plain_404(anon, shares):
    _quotes(anon)
    for missing in ("aaaaaaaaaa", "not-an-id", "A" * 10, "aaaaaaaaaaaaaaaaaaaaaa"):
        assert anon.get(f"/q/{missing}").status_code == 404, missing
        assert anon.get(f"/q/{missing}.png").status_code == 404, missing
    assert anon.get("/api/public/quote-shares/aaaaaaaaaa").status_code == 404


def test_card_is_a_png_rendered_once(anon, shares, monkeypatch):
    sid = _quotes(anon)[GALLOWAY_LINE]["share_id"]
    monkeypatch.setattr(QuoteShareService, "_artwork", lambda self, share: None)  # no network in tests
    first = anon.get(f"/q/{sid}.png")
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/png"
    assert first.headers["cache-control"] == "public, max-age=86400"
    assert Image.open(io.BytesIO(first.content)).size == (1200, 630)

    from thestill.services import quote_share_service

    def never(card):
        raise AssertionError("rendered again instead of served from the cache")

    monkeypatch.setattr(quote_share_service, "render_og", never)
    again = anon.get(f"/q/{sid}.png")
    assert again.content == first.content
    assert anon.get(f"/q/{sid}.png", headers={"if-none-match": first.headers["etag"]}).status_code == 304


def test_switch_off_serves_cached_cards_and_renders_no_new_ones(anon, shares, app_state, monkeypatch):
    quotes = _quotes(anon)
    monkeypatch.setattr(QuoteShareService, "_artwork", lambda self, share: None)
    cached, cold = quotes[GALLOWAY_LINE]["share_id"], quotes[ELSON_LINE]["share_id"]
    assert anon.get(f"/q/{cached}.png").status_code == 200
    app_state.abuse.settings.set("quote_cards_enabled", False, actor=Actor.cli())
    assert anon.get(f"/q/{cached}.png").status_code == 200
    assert anon.get(f"/q/{cold}.png").status_code == 503
    # The link itself still leads to the episode.
    assert anon.get(f"/q/{cold}").status_code == 200


def test_card_etag_changes_with_the_speaker_name(anon, shares, app_state):
    sid = _quotes(anon)[GALLOWAY_LINE]["share_id"]
    before = shares.card_etag(shares.resolve(sid))
    from thestill.models.entities import EntityRecord, EntityType

    app_state.entity_repository.upsert_entity(
        EntityRecord(id="person:scott-galloway", type=EntityType.PERSON, canonical_name="Prof G")
    )
    after = shares.resolve(sid)
    assert after.speaker_name == "Prof G"
    assert shares.card_etag(after) != before


def test_shared_quote_lookup_for_the_landing(anon, shares):
    sid = _quotes(anon)[ELSON_LINE]["share_id"]
    body = anon.get(f"/api/public/quote-shares/{sid}").json()["share"]
    assert body == {
        "id": sid,
        "text": ELSON_LINE,
        "start_seconds": 185,
        "speaker": {"name": "Ed Elson", "slug": "ed-elson"},
        "episode": {"slug": "summarised", "title": "Episode 1"},
        "podcast": {"slug": "public-show", "title": body["podcast"]["title"]},
    }


def test_revoke_makes_the_link_410_and_deletes_the_card(anon, shares, app_config, monkeypatch):
    sid = _quotes(anon)[GALLOWAY_LINE]["share_id"]
    monkeypatch.setattr(QuoteShareService, "_artwork", lambda self, share: None)
    assert anon.get(f"/q/{sid}.png").status_code == 200
    card_dir = app_config.path_manager.quote_cards_dir() / sid
    assert list(card_dir.glob("*.png"))

    assert shares.revoke(sid, actor=Actor.cli(), reason="wrong speaker") is True
    assert shares.revoke(sid, actor=Actor.cli(), reason="again") is False
    assert not list(card_dir.glob("*.png"))
    for path in (f"/q/{sid}", f"/q/{sid}.png", f"/api/public/quote-shares/{sid}"):
        assert anon.get(path).status_code == 410, path
    # A revoked quote is not offered again.
    from thestill.web.public_access import clear_public_caches

    clear_public_caches()
    assert _quotes(anon)[GALLOWAY_LINE]["share_id"] is None


def test_show_made_private_withdraws_its_links(anon, shares, app_state):
    quotes = _quotes(anon)
    withdrawn = shares.revoke_for_podcast("11111111-1111-1111-1111-111111111111", reason="show made private")
    assert set(withdrawn) == {quote["share_id"] for quote in quotes.values()}
    assert anon.get(f"/q/{withdrawn[0]}").status_code == 410


def test_hidden_speaker_withdraws_the_link(anon, shares, app_state):
    sid = _quotes(anon)[GALLOWAY_LINE]["share_id"]
    app_state.entity_repository.set_entity_public_hidden("person:scott-galloway", True)
    assert anon.get(f"/q/{sid}").status_code == 410


def test_a_merged_speaker_is_followed(anon, shares, app_state):
    sid = _quotes(anon)[ELSON_LINE]["share_id"]
    from thestill.models.entities import EntityRecord, EntityType

    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="person:edward-elson", type=EntityType.PERSON, canonical_name="Edward Elson"))
    repo.delete_entity("person:ed-elson", redirect_to="person:edward-elson")
    assert shares.resolve(sid).speaker_name == "Edward Elson"


def test_robots_leaves_quote_links_fetchable(anon, shares):
    """Twitterbot obeys robots.txt: a disallowed /q/ would preview as nothing."""
    parser = urllib.robotparser.RobotFileParser()
    from thestill.web import public_shell

    parser.parse(public_shell.ROBOTS_PUBLIC.format(base="https://thestill.test").splitlines())
    assert parser.can_fetch("Twitterbot", "https://thestill.test/q/aaaaaaaaaa")
    assert parser.can_fetch("LinkedInBot", "https://thestill.test/q/aaaaaaaaaa.png")
