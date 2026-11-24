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


def test_read_only_serves_cached_cards_and_renders_no_new_ones(anon, shares, app_state, monkeypatch):
    quotes = _quotes(anon)
    monkeypatch.setattr(QuoteShareService, "_artwork", lambda self, share: None)
    cached, cold = quotes[GALLOWAY_LINE]["share_id"], quotes[ELSON_LINE]["share_id"]
    assert anon.get(f"/q/{cached}.png").status_code == 200
    app_state.abuse.settings.set("read_only", True, actor=Actor.cli())
    assert anon.get(f"/q/{cached}.png").status_code == 200
    assert anon.get(f"/q/{cold}.png").status_code == 503
    assert not (anon.app.state.app_state.config.path_manager.quote_cards_dir() / cold).exists()


def test_hiding_a_speaker_deletes_their_cards(anon, shares, app_state, app_config, monkeypatch):
    sid = _quotes(anon)[GALLOWAY_LINE]["share_id"]
    monkeypatch.setattr(QuoteShareService, "_artwork", lambda self, share: None)
    assert anon.get(f"/q/{sid}.png").status_code == 200
    card_dir = app_config.path_manager.quote_cards_dir() / sid
    assert list(card_dir.glob("*.png"))
    app_state.entity_repository.set_entity_public_hidden("person:scott-galloway", True)
    assert shares.forget_speaker_cards("person:scott-galloway") == 1
    assert not list(card_dir.glob("*.png"))


def _respeak(app_state, segment: int, who: str) -> None:
    """Hand a turn to another speaker, as a transcript correction plus the
    entity refresh would."""
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute(
            "UPDATE entity_mentions SET entity_id = ? WHERE episode_id = ? AND segment_id = ? AND role = 'speaking'",
            (who, EPISODE_ID, segment),
        )


def test_refresh_moves_a_share_to_the_corrected_speaker_and_drops_its_card(
    anon, shares, app_state, app_config, monkeypatch
):
    sid = _quotes(anon)[GALLOWAY_LINE]["share_id"]
    monkeypatch.setattr(QuoteShareService, "_artwork", lambda self, share: None)
    assert anon.get(f"/q/{sid}.png").status_code == 200
    _respeak(app_state, 2, "person:ed-elson")

    counts = shares.reconcile_episode(EPISODE_ID)
    assert counts["changed"] == 1 and counts["unnamed"] == 0
    assert shares.resolve(sid).speaker_name == "Ed Elson"
    assert not list((app_config.path_manager.quote_cards_dir() / sid).glob("*.png"))
    # Stable: a second pass changes nothing.
    assert shares.reconcile_episode(EPISODE_ID)["changed"] == 0


def test_refresh_follows_the_words_through_a_split_and_ignores_a_repeat_elsewhere(anon, shares, app_state):
    sid = _quotes(anon)[GALLOWAY_LINE]["share_id"]
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        # The turn (segment 2, 120-140 s) is split: its second half is now
        # segment 7 at 130 s, spoken by Ed, and holds the quoted words.
        conn.execute(
            "UPDATE entity_mentions SET segment_id = 7, start_ms = 130000 "
            "WHERE episode_id = ? AND segment_id = 2 AND quote_excerpt = ?",
            (EPISODE_ID, GALLOWAY_LINE),
        )
        conn.execute(
            "INSERT INTO entity_mentions (entity_id, episode_id, segment_id, start_ms, end_ms, speaker, role,"
            " surface_form, quote_excerpt, confidence, extractor, resolution_status)"
            " SELECT 'person:ed-elson', episode_id, 7, 130000, 140000, 'Ed Elson', role, 'Ed Elson', quote_excerpt,"
            " confidence, extractor, resolution_status FROM entity_mentions"
            " WHERE episode_id = ? AND segment_id = 2 AND role = 'speaking'",
            (EPISODE_ID,),
        )
    shares.reconcile_episode(EPISODE_ID)
    assert shares.resolve(sid).speaker_name == "Ed Elson"

    # The words are then edited away in that turn, while another speaker
    # says the same words minutes later: never taken for the speaker.
    with app_state.repository._get_connection() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE entity_mentions SET segment_id = 3, start_ms = 185000 WHERE episode_id = ? AND quote_excerpt = ?",
            (EPISODE_ID, GALLOWAY_LINE),
        )
    assert shares.reconcile_episode(EPISODE_ID)["unnamed"] >= 1
    assert shares.resolve(sid).speaker_name is None
    assert "A guest on " in anon.get(f"/q/{sid}").text


def test_a_share_is_reported_for_browsers_with_the_method_only(anon, app_state):
    captured = []

    class Sink:
        enabled = True
        browser_key = None

        def capture(self, event, *, user=None, visitor_key=None, gpc=False):
            captured.append(event)

        def shutdown(self):
            pass

    app_state.analytics_sink = Sink()
    assert anon.post("/api/public/quote-shared", json={"method": "link"}, headers=CHROME).status_code == 204
    assert (
        anon.post("/api/public/quote-shared", json={"method": "share"}, headers={"user-agent": "Googlebot"}).status_code
        == 204
    )
    assert [(event.name, event.properties()) for event in captured] == [("public_quote_shared", {"method": "link"})]
    # An extra field is dropped, never forwarded; an unknown method is refused.
    assert (
        anon.post("/api/public/quote-shared", json={"method": "share", "url": "x"}, headers=CHROME).status_code == 204
    )
    assert captured[-1].properties() == {"method": "share"}
    assert anon.post("/api/public/quote-shared", json={"method": "email"}, headers=CHROME).status_code == 422
    assert len(captured) == 2


@pytest.fixture
def reader(app_config, app_state):
    """A signed-in reader: the session dependency answers with a plain user."""
    from fastapi.testclient import TestClient

    from thestill.models.user import User
    from thestill.web.app import create_app
    from thestill.web.dependencies import require_auth

    app = create_app(app_config)
    app.state.app_state = app_state
    app.dependency_overrides[require_auth] = lambda: User(id="user-plain", email="p@example.com", name="P")
    return TestClient(app)


READER_SHARES = "/api/podcasts/public-show/episodes/summarised/quote-shares"


def test_reader_gets_the_public_quotes_of_an_episode_with_their_turns(reader, anon, shares):
    """Spec #114 Rule A: the transcript offers only quotes already public."""
    body = reader.get(READER_SHARES).json()["shares"]
    public = {quote["text"]: quote for quote in _quotes(anon).values()}
    assert {share["text"] for share in body} == set(public)
    for share in body:
        assert share["id"] == public[share["text"]]["share_id"]
    galloway = next(share for share in body if share["text"] == GALLOWAY_LINE)
    assert (galloway["segment_id"], galloway["start_seconds"], galloway["speaker"]) == (2, 125, "Scott Galloway")
    # The sponsor read, the unnamed speaker and the hidden person offer nothing.
    assert len(body) == 2


def test_reader_gets_nothing_where_there_is_nothing_public(reader, anon, shares, app_state):
    app_state.repository.set_podcast_public_pages("11111111-1111-1111-1111-111111111111", False)
    assert reader.get(READER_SHARES).json()["shares"] == []
    assert reader.get("/api/podcasts/public-show/episodes/no-such-episode/quote-shares").status_code == 404


def test_reader_shares_need_a_session(anon, shares):
    assert anon.get(READER_SHARES).status_code == 401


# Two spaces and a line break inside, as an excerpt can carry them; the card collapses them.
SPOKEN_LINE = (
    "So, you know, Nvidia owns the data centre  now, and nobody else is anywhere\nclose to catching up with it."
)


def test_a_spoken_quote_gets_a_card_with_its_opener_dropped_and_keeps_its_speaker(anon, shares, app_state):
    from thestill.models.entities import EntityMention, MentionRole

    episode_id = EPISODE_ID
    app_state.entity_repository.insert_mentions(
        [
            EntityMention(
                entity_id="person:ed-elson",
                episode_id=episode_id,
                segment_id=8,
                start_ms=480_000,
                end_ms=500_000,
                speaker="Ed Elson",
                role=MentionRole.SPEAKING,
                surface_form="Ed Elson",
                quote_excerpt="First sentence only.",
                confidence=1.0,
                extractor="speaker:synth",
            ),
            EntityMention(
                entity_id="company:nvidia",
                episode_id=episode_id,
                segment_id=8,
                start_ms=485_000,
                end_ms=488_000,
                speaker="Ed Elson",
                role=MentionRole.MENTIONED,
                surface_form="Nvidia",
                quote_excerpt=SPOKEN_LINE,
                confidence=0.9,
                extractor="gliner:test",
            ),
        ]
    )
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute("UPDATE entity_mentions SET resolution_status = 'resolved' WHERE entity_id IS NOT NULL")

    listed = next(quote for text, quote in _quotes(anon).items() if text.startswith("So, you know, Nvidia"))
    # The page lists the quote as spoken; the card has the written form.
    share = anon.get(f"/api/public/quote-shares/{listed['share_id']}").json()["share"]
    assert share["text"] == "Nvidia owns the data centre now, and nobody else is anywhere close to catching up with it."
    assert share["speaker"]["name"] == "Ed Elson"

    # The speaker re-check still finds the trimmed words in their turn.
    _respeak(app_state, 8, "person:scott-galloway")
    assert shares.reconcile_episode(EPISODE_ID)["unnamed"] == 0
    assert shares.resolve(listed["share_id"]).speaker_name == "Scott Galloway"
