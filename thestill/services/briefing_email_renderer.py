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

"""
Briefing email renderer (spec #51).

Turns a briefing's ``script.md`` into a standalone email: an inline-styled
HTML part plus a plain-text alternative, both with absolute URLs built
from ``PUBLIC_BASE_URL`` (the script's links are app-relative), a "Read in
app" link, and a signed one-click unsubscribe footer with the matching
``List-Unsubscribe`` / RFC 8058 headers.

The markdown→HTML conversion is a small purpose-built pass over the exact
constructs ``BriefingScriptGenerator`` emits (headings, links, bold, list
items, horizontal rules, paragraphs) — the project has no general markdown
dependency and the email must render deterministically. Unknown constructs
degrade to escaped paragraph text, never to broken markup. v1 ships the
full script as the body (the reader is the deliverable); an audio link
slot is reserved for #34.
"""

import html
from typing import Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..models.briefing import Briefing
from ..utils.unsubscribe_token import make_unsubscribe_token
from .email_markup import (
    STYLE_BODY,
    STYLE_BUTTON,
    STYLE_CARD,
    STYLE_FOOTER,
    STYLE_LINK,
    RenderedEmail,
    absolutize_links,
    markdown_to_html,
)

__all__ = ["BriefingEmailRenderer", "RenderedEmail"]


class BriefingEmailRenderer:
    """Render a briefing script into email parts (spec #51)."""

    def __init__(self, *, public_base_url: str, secret: str) -> None:
        if not public_base_url:
            raise ValueError("PUBLIC_BASE_URL is required for briefing email delivery")
        if not secret:
            raise ValueError("JWT_SECRET_KEY is required to sign unsubscribe links")
        self._base_url = public_base_url.rstrip("/")
        self._secret = secret

    def render(
        self,
        briefing: Briefing,
        script_markdown: str,
        *,
        timezone_name: Optional[str] = None,
    ) -> RenderedEmail:
        """Render the briefing script into (subject, html, text, headers).

        ``timezone_name`` (the schedule's IANA zone) localizes the subject
        date — ``created_at`` is UTC, and a UTC calendar date is yesterday
        for a morning slot east of Greenwich.
        """
        subject_date = briefing.created_at
        if timezone_name:
            try:
                subject_date = briefing.created_at.astimezone(ZoneInfo(timezone_name))
            except (KeyError, ValueError):
                pass  # Unknown zone: fall back to the UTC date.
        episode_word = "episode" if briefing.episode_count == 1 else "episodes"
        subject = (
            f"Your briefing — {briefing.episode_count} new {episode_word}"
            f" ({subject_date.strftime('%b')} {subject_date.day})"
        )

        absolute_markdown = absolutize_links(script_markdown, self._base_url)
        briefing_url = f"{self._base_url}/briefings"
        unsubscribe_url = self._unsubscribe_url(briefing.user_id)

        text = (
            f"{absolute_markdown.rstrip()}\n\n"
            f"---\n\n"
            f"Read in app: {briefing_url}\n"
            f"Stop receiving these emails: {unsubscribe_url}\n"
        )

        html_body = markdown_to_html(absolute_markdown)
        html_doc = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>\n'
            f'<body style="{STYLE_BODY}">\n'
            f'<div style="{STYLE_CARD}">\n'
            f"{html_body}\n"
            f'<p><a href="{html.escape(briefing_url, quote=True)}" style="{STYLE_BUTTON}">Read in app</a></p>\n'
            # Spec #34 interlock: the audio link slot renders here once
            # briefing audio ships (hosted file or personal-feed player).
            f'<div style="{STYLE_FOOTER}">\n'
            "<p>You are receiving this because briefing email delivery is enabled in your "
            "Thestill settings.</p>\n"
            f'<p><a href="{html.escape(unsubscribe_url, quote=True)}" style="{STYLE_LINK}">Unsubscribe</a> '
            "with one click — no login needed.</p>\n"
            "</div>\n</div>\n</body>\n</html>\n"
        )

        headers = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            # RFC 8058 — mail clients POST to the URL for one-click.
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            "Auto-Submitted": "auto-generated",
        }
        return RenderedEmail(subject=subject, html=html_doc, text=text, headers=headers)

    def _unsubscribe_url(self, user_id: str) -> str:
        token = make_unsubscribe_token(user_id, self._secret)
        return f"{self._base_url}/unsubscribe/briefings?token={quote(token, safe='')}"
