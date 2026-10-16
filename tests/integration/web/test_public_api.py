# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Spec #94 Phase A — the signed-out public API under ``/api/public``.

The app runs in multi-user mode (the only mode that mounts it) against a
tmp SQLite corpus: one public show with a summarised episode, an episode
with no summary, an ``N/A`` summary, and a hidden show.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from thestill.models.podcast import Episode, Podcast
from thestill.utils.config import Config
from thestill.web import dependencies
from thestill.web.app import create_app
from thestill.web.middleware import rate_limit
from thestill.web.public_access import clear_public_caches, get_public_visibility
from thestill.web.routes import api_public

REAL_SUMMARY = "## 1. 🎙️ The Gist\n\nTwo hosts argue about chips.\n\n## 2. Timeline\n\n- [01:00] Start\n"
FORBIDDEN_KEYS = {
    "rss_url",
    "state",
    "external_id",
    "refresh_pending",
    "is_following",
    "failure_reason",
    "failed_at_stage",
    "origin",
    "import_kind",
    "playback",
    "user_region",
}


@pytest.fixture
def app_config(tmp_path: Path) -> Config:
    return Config(
        storage_path=tmp_path,
        multi_user=True,
        public_base_url="https://thestill.test",
        google_client_id="test-client",
        google_client_secret="test-secret",
        jwt_secret_key="test" * 16,
        cookie_secure=False,
    )


@pytest.fixture(autouse=True)
def _fresh_buckets() -> Iterator[None]:
    rate_limit.reset_for_testing()
    clear_public_caches()
    yield
    rate_limit.reset_for_testing()


@pytest.fixture
def anon(app_config: Config, app_state) -> TestClient:
    app = create_app(app_config)
    app.state.app_state = app_state
    return TestClient(app)


def _write_summary(app_state, name: str, content: str) -> str:
    path = app_state.path_manager.summary_file(name)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")
    return name


def _seed(app_state) -> None:
    now = datetime.now(timezone.utc)

    def episode(n: int, slug: str, summary: str | None, preview: str | None) -> Episode:
        return Episode(
            id=f"00000000-0000-0000-0000-00000000000{n}",
            external_id=f"guid-{n}",
            title=f"Episode {n}",
            description="Publisher notes.",
            slug=slug,
            pub_date=now - timedelta(days=n),
            audio_url=f"https://example.com/{n}.mp3",
            summary_path=_write_summary(app_state, f"{slug}_summary.md", summary) if summary else None,
            summary_preview=preview,
            raw_transcript_path=f"{slug}_transcript.json",
            clean_transcript_path=f"{slug}_clean.md",
        )

    public = Podcast(
        id="11111111-1111-1111-1111-111111111111",
        rss_url="https://example.com/public.xml",
        title="Public Show",
        description="A show about chips.",
        slug="public-show",
        created_at=now,
        episodes=[
            episode(1, "summarised", REAL_SUMMARY, "Two hosts argue about chips."),
            episode(2, "no-summary", None, None),
            episode(3, "not-applicable", "N/A — this episode is a trailer.", ""),
        ],
    )
    hidden = Podcast(
        id="22222222-2222-2222-2222-222222222222",
        rss_url="https://example.com/hidden.xml",
        title="Hidden Show",
        description="",
        slug="hidden-show",
        created_at=now,
        episodes=[episode(4, "hidden-episode", REAL_SUMMARY, "Two hosts argue about chips.")],
    )
    for podcast in (public, hidden):
        app_state.repository.save(podcast)
        for ep in podcast.episodes:
            if ep.summary_preview is not None:
                app_state.repository.set_episode_summary_preview(ep.id, ep.summary_preview)
    app_state.repository.set_podcast_public_pages(hidden.id, False)


def _walk_keys(value) -> set:
    keys: set = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(k)
            keys |= _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            keys |= _walk_keys(v)
    return keys


def _assert_clean(body: dict) -> None:
    keys = _walk_keys(body)
    assert not keys & FORBIDDEN_KEYS, keys & FORBIDDEN_KEYS
    assert not [k for k in keys if k.endswith("_path")]


def test_public_podcast_is_readable_without_a_session(anon, app_state):
    _seed(app_state)
    response = anon.get("/api/public/podcasts/public-show")
    assert response.status_code == 200, response.text
    podcast = response.json()["podcast"]
    assert podcast["title"] == "Public Show"
    assert podcast["summarised_count"] == 1
    _assert_clean(response.json())
    assert response.headers["Cache-Control"].startswith("public")
    assert response.headers["Vary"] == "Cookie, Authorization"


def test_hidden_and_unknown_podcasts_are_404(anon, app_state):
    _seed(app_state)
    assert anon.get("/api/public/podcasts/hidden-show").status_code == 404
    assert anon.get("/api/public/podcasts/hidden-show/episodes/hidden-episode").status_code == 404
    assert anon.get("/api/public/podcasts/nope").status_code == 404
    assert anon.get("/api/public/podcasts/Not_A_Slug").status_code == 404


def test_episode_list_has_only_real_summaries(anon, app_state):
    _seed(app_state)
    body = anon.get("/api/public/podcasts/public-show/episodes").json()
    assert [e["slug"] for e in body["episodes"]] == ["summarised"]
    assert body["total"] == 1
    _assert_clean(body)


def test_episode_without_a_real_summary_is_404(anon, app_state):
    _seed(app_state)
    assert anon.get("/api/public/podcasts/public-show/episodes/no-summary").status_code == 404
    assert anon.get("/api/public/podcasts/public-show/episodes/not-applicable").status_code == 404
    assert anon.get("/api/public/podcasts/public-show/episodes/not-applicable/summary").status_code == 404


def test_public_episode_shape(anon, app_state):
    _seed(app_state)
    response = anon.get("/api/public/podcasts/public-show/episodes/summarised")
    assert response.status_code == 200, response.text
    episode = response.json()["episode"]
    assert episode["has_transcript"] is True
    assert episode["podcast"]["slug"] == "public-show"
    _assert_clean(response.json())


def test_summary_never_calls_an_llm_or_translates(anon, app_state, monkeypatch):
    _seed(app_state)

    def _boom(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("the public summary route must not build an LLM provider")

    monkeypatch.setattr("thestill.core.llm_provider.create_llm_provider_from_config", _boom)
    response = anon.get("/api/public/podcasts/public-show/episodes/summarised/summary")
    assert response.status_code == 200, response.text
    assert "Two hosts argue" in response.json()["content"]
    assert "citations" not in response.json()
    assert anon.get("/api/public/podcasts/public-show/episodes/summarised/summary?lang=fr").status_code == 404


def test_opening_a_show_never_enqueues_a_refresh(anon, app_state, monkeypatch):
    _seed(app_state)

    def _boom(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("public reads must not trigger refresh-on-open")

    monkeypatch.setattr(app_state.refresh_on_open, "maybe_trigger", _boom)
    assert anon.get("/api/public/podcasts/public-show").status_code == 200


def test_recent_summaries_feed(anon, app_state):
    _seed(app_state)
    body = anon.get("/api/public/recent-summaries?limit=6").json()
    assert [e["slug"] for e in body["episodes"]] == ["summarised"]
    assert body["episodes"][0]["podcast"]["slug"] == "public-show"
    assert anon.get("/api/public/recent-summaries?limit=50").status_code == 422


def test_credentialed_requests_are_private(anon, app_state):
    _seed(app_state)
    for headers in ({"Authorization": "Bearer not-a-real-token"}, {"Cookie": f"{dependencies.AUTH_COOKIE_NAME}=x"}):
        response = anon.get("/api/public/podcasts/public-show", headers=headers)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-cache"
        assert response.headers["Vary"] == "Cookie, Authorization"


def test_anonymous_callers_are_rate_limited(anon, app_state, monkeypatch):
    _seed(app_state)
    monkeypatch.setattr(rate_limit, "PUBLIC_LIMIT", rate_limit.RateLimit(max_events=3, window_seconds=60))
    codes = [anon.get("/api/public/podcasts/public-show").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_only_public_and_auth_routes_skip_authentication(app_config, app_state):
    """Default-deny holds: every /api route needs a session unless it is on
    the written allowlist (spec #94 FM: a new router silently public)."""
    app = create_app(app_config)
    guards = {dependencies.require_auth, dependencies.require_admin}

    def guarded(route: APIRoute) -> bool:
        stack = list(route.dependant.dependencies)
        while stack:
            dep = stack.pop()
            if dep.call in guards:
                return True
            stack.extend(dep.dependencies)
        return False

    open_routes = sorted(
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/") and not guarded(route)
    )
    assert open_routes
    assert all(path.startswith(("/api/public/", "/api/auth/")) for path in open_routes), open_routes


def test_single_user_mode_mounts_no_public_api(tmp_path):
    config = Config(storage_path=tmp_path, multi_user=False, jwt_secret_key="test" * 16, cookie_secure=False)
    app = create_app(config)
    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/api/public")]


# ---------------------------------------------------------------------------
# Phase D — people, entities and quotes
# ---------------------------------------------------------------------------

GALLOWAY_LINE = (
    "I think Nvidia has built the deepest moat in enterprise computing, and the market is only starting to price it."
)
ELSON_LINE = (
    "Scott keeps saying Nvidia is overvalued, but the data centre numbers keep proving him wrong every quarter."
)


def _seed_people(app_state) -> None:
    from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole

    _seed(app_state)
    repo = app_state.entity_repository
    for entity_id, kind, name in (
        ("person:scott-galloway", EntityType.PERSON, "Scott Galloway"),
        ("person:ed-elson", EntityType.PERSON, "Ed Elson"),
        ("person:hidden-person", EntityType.PERSON, "Hidden Person"),
        ("company:nvidia", EntityType.COMPANY, "Nvidia"),
    ):
        repo.upsert_entity(EntityRecord(id=entity_id, type=kind, canonical_name=name))
    episode_id = "00000000-0000-0000-0000-000000000001"

    def speaking(segment: int, who: str, name: str) -> EntityMention:
        return EntityMention(
            entity_id=who,
            episode_id=episode_id,
            segment_id=segment,
            start_ms=segment * 60_000,
            end_ms=segment * 60_000 + 20_000,
            speaker=name,
            role=MentionRole.SPEAKING,
            surface_form=name,
            quote_excerpt="First sentence only.",
            confidence=1.0,
            extractor="speaker:synth",
        )

    def named(segment: int, about: str, speaker: str, quote: str) -> EntityMention:
        return EntityMention(
            entity_id=about,
            episode_id=episode_id,
            segment_id=segment,
            start_ms=segment * 60_000 + 5_000,
            end_ms=segment * 60_000 + 8_000,
            speaker=speaker,
            role=MentionRole.MENTIONED,
            surface_form="Nvidia",
            quote_excerpt=quote,
            confidence=0.9,
            extractor="gliner:test",
        )

    repo.insert_mentions(
        [
            speaking(2, "person:scott-galloway", "Scott Galloway"),
            named(2, "company:nvidia", "Scott Galloway", GALLOWAY_LINE),
            speaking(3, "person:ed-elson", "Ed Elson"),
            named(3, "company:nvidia", "Ed Elson", ELSON_LINE),
            named(3, "person:scott-galloway", "Ed Elson", ELSON_LINE),
            # A sponsor read by the host is never a public quote.
            speaking(4, "person:scott-galloway", "Scott Galloway"),
            named(
                4,
                "company:nvidia",
                "Scott Galloway",
                "This episode is brought to you by Nvidia, use code PROFG for ten percent off today.",
            ),
            # An unnamed speaker's segment has no resolved speaking row.
            named(
                5,
                "company:nvidia",
                "SPEAKER_03",
                "Nvidia is a company that somebody we cannot name talked about at great length.",
            ),
            # A hidden person's quotes disappear everywhere.
            speaking(6, "person:hidden-person", "Hidden Person"),
            named(
                6,
                "company:nvidia",
                "Hidden Person",
                "Nvidia is something the hidden person has a long and detailed opinion about, apparently.",
            ),
        ]
    )
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute("UPDATE entity_mentions SET resolution_status = 'resolved' WHERE entity_id IS NOT NULL")
    repo.set_entity_public_hidden("person:hidden-person", True)


def test_person_page_payload(anon, app_state):
    _seed_people(app_state)
    response = anon.get("/api/public/entities/person/scott-galloway")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entity"]["name"] == "Scott Galloway"
    assert body["featured_quote"]["text"] == GALLOWAY_LINE
    assert body["quote_counts"] == {"said": 1, "about": 1}
    assert [t["name"] for t in body["topics"]] == ["Nvidia"]
    assert body["stats"]["shows"] == 1
    _assert_clean(body)


def test_person_quote_streams(anon, app_state):
    _seed_people(app_state)
    said = anon.get("/api/public/entities/person/scott-galloway/quotes?kind=said").json()
    assert [q["text"] for q in said["quotes"]] == [GALLOWAY_LINE]
    filtered = anon.get("/api/public/entities/person/scott-galloway/quotes?kind=said&about=company:nvidia").json()
    assert filtered["total"] == 1
    about = anon.get("/api/public/entities/person/scott-galloway/quotes?kind=about").json()
    assert [(q["speaker"]["name"], q["text"]) for q in about["quotes"]] == [("Ed Elson", ELSON_LINE)]


def test_entity_page_whos_talking_excludes_sponsor_unnamed_and_hidden(anon, app_state):
    _seed_people(app_state)
    body = anon.get("/api/public/entities/company/nvidia").json()
    names = sorted(s["name"] for s in body["speakers"])
    assert names == ["Ed Elson", "Scott Galloway"]
    stream = anon.get("/api/public/entities/company/nvidia/quotes").json()
    texts = [q["text"] for q in stream["quotes"]]
    assert sorted(texts) == sorted([GALLOWAY_LINE, ELSON_LINE])
    assert len(body["activity"]["weeks"]) == 26


def test_hidden_entities_are_404(anon, app_state):
    _seed_people(app_state)
    assert anon.get("/api/public/entities/person/hidden-person").status_code == 404
    assert anon.get("/api/public/entities/person/nobody").status_code == 404
    assert anon.get("/api/public/entities/planet/earth").status_code == 404


def test_episode_quotes_and_linked_quote(anon, app_state):
    _seed_people(app_state)
    body = anon.get("/api/public/podcasts/public-show/episodes/summarised/quotes?q=125").json()
    assert {q["speaker"]["name"] for q in body["quotes"]} == {"Scott Galloway", "Ed Elson"}
    assert body["linked"]["text"] == GALLOWAY_LINE
    assert "Hidden Person" not in {m["name"] for m in body["mentioned"]}


def test_who_said_what_picks_a_thing_with_several_speakers(anon, app_state):
    _seed_people(app_state)
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute(
            "UPDATE episodes SET pub_date = ? WHERE slug = 'summarised'", (datetime.now(timezone.utc).isoformat(),)
        )
    body = anon.get("/api/public/who-said-what").json()
    assert body["entity"]["name"] == "Nvidia"
    assert body["speakers"] == 2
    # One show here, so one quote: never two from the same show.
    assert len(body["quotes"]) == 1


def test_a_takedown_from_another_process_reaches_cached_payloads(anon, app_state):
    _seed(app_state)
    assert [e["slug"] for e in anon.get("/api/public/recent-summaries").json()["episodes"]] == ["summarised"]
    # The takedown CLI runs in its own process: this server learns of it only
    # when its hidden-set TTL lapses (simulated by expiring just that value),
    # and the still-fresh payload cache must not outlive it.
    app_state.repository.set_podcast_public_pages("11111111-1111-1111-1111-111111111111", False)
    get_public_visibility(app_state)._hidden_podcasts.clear()  # noqa: SLF001
    assert anon.get("/api/public/recent-summaries").json()["episodes"] == []


def test_sign_in_returns_to_next_once(anon, app_state, monkeypatch):
    auth = app_state.auth_service
    monkeypatch.setattr(auth, "get_google_auth_url", lambda redirect_uri: ("https://accounts.example/o", "st"))

    async def callback(code, redirect_uri):
        return SimpleNamespace(email="reader@example.com"), "jwt-token", False

    async def no_region(user, ip):
        return None

    monkeypatch.setattr(auth, "handle_google_callback", callback)
    monkeypatch.setattr(auth, "maybe_infer_region", no_region)

    login = anon.get("/api/auth/google/login", params={"next": "/entities/person/x"}, follow_redirects=False)
    assert login.status_code == 302
    assert anon.cookies.get("oauth_next", "").strip('"') == "/entities/person/x"
    done = anon.get("/api/auth/google/callback", params={"code": "c", "state": "st"}, follow_redirects=False)
    assert done.status_code == 302
    assert done.headers["location"] == "/entities/person/x"
    assert anon.cookies.get("oauth_next") is None

    # An abandoned flow's return path never steers a later plain sign-in.
    anon.cookies.set("oauth_next", "/inbox", domain="testserver.local")
    anon.get("/api/auth/google/login", follow_redirects=False)
    assert anon.cookies.get("oauth_next") is None
    assert (
        anon.get("/api/auth/google/callback", params={"code": "c", "state": "st"}, follow_redirects=False).headers[
            "location"
        ]
        == "/"
    )


def test_sitemap_and_page_count_quotes_the_same_way(anon, app_state):
    # One rule decides whether a person or entity page is indexed (FM-6):
    # the sitemap's SQL count must equal the page's said + about count.
    _seed_people(app_state)
    sql_counts = {
        str(row["entity_id"]): int(row["quotes"])
        for row in app_state.entity_repository.public_quote_queries().indexable_entities(min_quotes=1)
    }
    for entity_type, slug in (("person", "scott-galloway"), ("person", "ed-elson"), ("company", "nvidia")):
        counts = anon.get(f"/api/public/entities/{entity_type}/{slug}").json()["quote_counts"]
        assert sql_counts.get(f"{entity_type}:{slug}", 0) == counts["said"] + counts["about"], slug


def test_top_chart_region_follows_the_visitor_then_the_us(anon, app_state, monkeypatch):
    import sqlite3

    from .conftest import seed_top_chart

    seed_top_chart(app_state, "gb", [{"rank": 1, "name": "The Rest Is History", "rss_url": "https://example.com/t"}])
    with sqlite3.connect(str(app_state.repository.db_path)) as conn:
        conn.execute(
            "INSERT INTO top_podcasts_meta (region, source_path, source_mtime, row_count, seeded_at) "
            "VALUES ('us', 'test', 0, 0, '2026-10-15T00:00:00+00:00')"
        )
    assert set(app_state.repository.get_top_podcast_regions()) == {"gb", "us"}
    calls: list = []

    async def from_ip(ip):
        calls.append(ip)
        return answers[ip]

    monkeypatch.setattr(api_public, "lookup_country_from_ip", from_ip)
    monkeypatch.setattr(api_public, "_visitor_country_cache", api_public.TtlCache(60.0))

    answers = {"81.2.69.142": "gb", "203.0.113.9": "zz", "198.51.100.7": None}
    # The client IP is whatever the proxy-aware resolver says; stand it in.
    headers = lambda ip: {"X-Test-Ip": ip}  # noqa: E731
    monkeypatch.setattr(api_public, "resolve_client_ip", lambda request: request.headers["X-Test-Ip"])

    uk = anon.get("/api/public/top-podcasts", headers=headers("81.2.69.142"))
    assert uk.json()["region"] == "gb"
    # Chosen from the IP, so no shared cache may store it.
    assert uk.headers["Cache-Control"] == "private, max-age=300"
    anon.get("/api/public/top-podcasts", headers=headers("81.2.69.142"))
    assert calls == ["81.2.69.142"]  # one lookup per address

    # A country with no chart, or no answer, falls back to the US.
    assert anon.get("/api/public/top-podcasts", headers=headers("203.0.113.9")).json()["region"] == "us"
    assert anon.get("/api/public/top-podcasts", headers=headers("198.51.100.7")).json()["region"] == "us"

    # An explicit choice wins and skips the lookup.
    chosen = anon.get("/api/public/top-podcasts?region=gb", headers=headers("198.51.100.7"))
    assert chosen.json()["region"] == "gb"
    assert chosen.headers["Cache-Control"].startswith("public")
    assert len(calls) == 3


def test_episode_quotes_page_filter_and_find_a_linked_quote_anywhere(anon, app_state, monkeypatch):
    _seed_people(app_state)
    monkeypatch.setattr(api_public, "_QUOTE_PAGE", 1)
    url = "/api/public/podcasts/public-show/episodes/summarised/quotes"

    first = anon.get(url, params={"q": 185}).json()
    assert [q["speaker"]["slug"] for q in first["quotes"]] == ["scott-galloway"]
    assert first["next_page"] == 2 and first["total"] == 2
    # The linked quote is found even though it is not on this page.
    assert first["linked"]["speaker"]["slug"] == "ed-elson"
    assert {s["slug"]: s["quotes"] for s in first["speakers"]} == {"scott-galloway": 1, "ed-elson": 1}

    second = anon.get(url, params={"page": 2}).json()
    assert [q["speaker"]["slug"] for q in second["quotes"]] == ["ed-elson"] and second["next_page"] is None

    elson = anon.get(url, params={"speaker": "ed-elson"}).json()
    assert [q["speaker"]["slug"] for q in elson["quotes"]] == ["ed-elson"] and elson["total"] == 1
    # The chips still list everyone quoted in the episode.
    assert len(elson["speakers"]) == 2


def test_auth_status_says_whether_the_public_site_is_on(app_config, app_state):
    app = create_app(app_config)
    app.state.app_state = app_state
    assert TestClient(app).get("/api/auth/status").json()["public_pages"] is True
    app_config.public_pages_enabled = False
    app = create_app(app_config)
    app.state.app_state = app_state
    assert TestClient(app).get("/api/auth/status").json()["public_pages"] is False
