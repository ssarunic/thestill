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

"""Unit tests for ``BriefingEmailRenderer`` (spec #51)."""

from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.briefing import Briefing
from thestill.services.briefing_email_renderer import BriefingEmailRenderer
from thestill.utils.unsubscribe_token import verify_unsubscribe_token

NOW = datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc)

SCRIPT = """# Morning Briefing
Generated: 2026-07-08 06:00 UTC

## Summary

- **Episodes processed:** 2 of 2
- **Podcasts updated:** 1

---

## 🎙️ Some Show (2 episodes)

### [First <Episode>](/podcasts/some-show/episodes/first)
**Published:** July 07, 2026 | **Duration:** 45m 0s

A look at **bold claims** & sharp elbows.

### [Second Episode](https://external.example.org/keep-me)

Already absolute — must not be rewritten.
"""


@pytest.fixture
def renderer():
    return BriefingEmailRenderer(public_base_url="https://app.example.com/", secret="test-secret")


@pytest.fixture
def briefing():
    return Briefing(
        user_id="user-123",
        cursor_from=NOW - timedelta(days=1),
        cursor_to=NOW,
        episode_count=2,
        created_at=NOW,
    )


class TestRender:
    def test_subject_names_count_and_date(self, renderer, briefing):
        email = renderer.render(briefing, SCRIPT)
        assert email.subject == "Your briefing — 2 new episodes (Jul 8)"

    def test_singular_episode_subject(self, renderer, briefing):
        briefing.episode_count = 1
        email = renderer.render(briefing, SCRIPT)
        assert "1 new episode (" in email.subject

    def test_subject_date_localized_to_schedule_timezone(self, renderer, briefing):
        # created_at is UTC; an Auckland morning slot generates on the
        # previous UTC calendar day — the subject must show the local date.
        briefing.created_at = datetime(2026, 7, 9, 20, 0, tzinfo=timezone.utc)
        email = renderer.render(briefing, SCRIPT, timezone_name="Pacific/Auckland")
        assert "(Jul 10)" in email.subject

    def test_unknown_timezone_falls_back_to_utc_date(self, renderer, briefing):
        email = renderer.render(briefing, SCRIPT, timezone_name="Mars/Olympus_Mons")
        assert "(Jul 8)" in email.subject

    def test_relative_links_become_absolute_in_both_parts(self, renderer, briefing):
        email = renderer.render(briefing, SCRIPT)
        assert 'href="https://app.example.com/podcasts/some-show/episodes/first"' in email.html
        assert "(https://app.example.com/podcasts/some-show/episodes/first)" in email.text

    def test_absolute_links_are_preserved(self, renderer, briefing):
        email = renderer.render(briefing, SCRIPT)
        assert 'href="https://external.example.org/keep-me"' in email.html
        assert "https://app.example.com/https://" not in email.html

    def test_html_is_escaped_and_markdown_converted(self, renderer, briefing):
        email = renderer.render(briefing, SCRIPT)
        # Angle brackets in the episode title must not become markup.
        assert "First &lt;Episode&gt;" in email.html
        assert "<strong>bold claims</strong>" in email.html
        assert "&amp; sharp elbows" in email.html
        assert "<h1" in email.html and "<h2" in email.html and "<h3" in email.html
        assert "<ul" in email.html and "<li>" in email.html

    def test_read_in_app_link_present(self, renderer, briefing):
        email = renderer.render(briefing, SCRIPT)
        assert 'href="https://app.example.com/briefings"' in email.html
        assert "Read in app: https://app.example.com/briefings" in email.text

    def test_unsubscribe_footer_and_headers(self, renderer, briefing):
        email = renderer.render(briefing, SCRIPT)
        assert email.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        url = email.headers["List-Unsubscribe"].strip("<>")
        assert url.startswith("https://app.example.com/unsubscribe/briefings?token=")
        token = url.split("token=", 1)[1]
        assert verify_unsubscribe_token(token, "test-secret") == "user-123"
        assert url in email.text

    def test_requires_base_url_and_secret(self):
        with pytest.raises(ValueError):
            BriefingEmailRenderer(public_base_url="", secret="s")
        with pytest.raises(ValueError):
            BriefingEmailRenderer(public_base_url="https://x", secret="")
