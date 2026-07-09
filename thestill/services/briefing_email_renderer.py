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
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..models.briefing import Briefing
from ..utils.unsubscribe_token import make_unsubscribe_token

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")

_STYLE_BODY = "margin:0;padding:0;background-color:#f4f4f5;"
_STYLE_CARD = (
    "max-width:600px;margin:0 auto;padding:24px;background-color:#ffffff;"
    "font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    "font-size:15px;line-height:1.6;color:#18181b;"
)
_STYLE_H1 = "font-size:22px;line-height:1.3;margin:0 0 12px;"
_STYLE_H2 = "font-size:18px;line-height:1.3;margin:24px 0 8px;"
_STYLE_H3 = "font-size:16px;line-height:1.3;margin:16px 0 4px;"
_STYLE_P = "margin:8px 0;"
_STYLE_HR = "border:none;border-top:1px solid #e4e4e7;margin:20px 0;"
_STYLE_LINK = "color:#1d4ed8;text-decoration:underline;"
_STYLE_BUTTON = (
    "display:inline-block;padding:10px 18px;margin:16px 0;background-color:#18181b;"
    "color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600;"
)
_STYLE_FOOTER = "margin-top:28px;padding-top:16px;border-top:1px solid #e4e4e7;font-size:12px;color:#71717a;"


@dataclass
class RenderedEmail:
    """The composed parts the delivery pass hands to ``EmailSender``."""

    subject: str
    html: str
    text: str
    headers: Dict[str, str] = field(default_factory=dict)


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

        absolute_markdown = self._absolutize_links(script_markdown)
        briefing_url = f"{self._base_url}/briefings"
        unsubscribe_url = self._unsubscribe_url(briefing.user_id)

        text = (
            f"{absolute_markdown.rstrip()}\n\n"
            f"---\n\n"
            f"Read in app: {briefing_url}\n"
            f"Stop receiving these emails: {unsubscribe_url}\n"
        )

        html_body = self._markdown_to_html(absolute_markdown)
        html_doc = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>\n'
            f'<body style="{_STYLE_BODY}">\n'
            f'<div style="{_STYLE_CARD}">\n'
            f"{html_body}\n"
            f'<p><a href="{html.escape(briefing_url, quote=True)}" style="{_STYLE_BUTTON}">Read in app</a></p>\n'
            # Spec #34 interlock: the audio link slot renders here once
            # briefing audio ships (hosted file or personal-feed player).
            f'<div style="{_STYLE_FOOTER}">\n'
            "<p>You are receiving this because briefing email delivery is enabled in your "
            "Thestill settings.</p>\n"
            f'<p><a href="{html.escape(unsubscribe_url, quote=True)}" style="{_STYLE_LINK}">Unsubscribe</a> '
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

    def _absolutize_links(self, markdown: str) -> str:
        """Rewrite app-relative link targets (``/podcasts/…``) to absolute."""

        def rewrite(match: "re.Match[str]") -> str:
            label, target = match.group(1), match.group(2)
            if target.startswith("/"):
                target = f"{self._base_url}{target}"
            return f"[{label}]({target})"

        return _LINK_RE.sub(rewrite, markdown)

    def _markdown_to_html(self, markdown: str) -> str:
        """Convert the briefing-script markdown subset to inline-styled HTML."""
        blocks: List[str] = []
        list_items: List[str] = []

        def flush_list() -> None:
            if list_items:
                blocks.append('<ul style="margin:8px 0;padding-left:24px;">' + "".join(list_items) + "</ul>")
                list_items.clear()

        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if not line:
                flush_list()
                continue
            if line == "---":
                flush_list()
                blocks.append(f'<hr style="{_STYLE_HR}">')
            elif line.startswith("### "):
                flush_list()
                blocks.append(f'<h3 style="{_STYLE_H3}">{self._inline(line[4:])}</h3>')
            elif line.startswith("## "):
                flush_list()
                blocks.append(f'<h2 style="{_STYLE_H2}">{self._inline(line[3:])}</h2>')
            elif line.startswith("# "):
                flush_list()
                blocks.append(f'<h1 style="{_STYLE_H1}">{self._inline(line[2:])}</h1>')
            elif line.startswith("- "):
                list_items.append(f"<li>{self._inline(line[2:])}</li>")
            else:
                flush_list()
                blocks.append(f'<p style="{_STYLE_P}">{self._inline(line)}</p>')
        flush_list()
        return "\n".join(blocks)

    def _inline(self, text: str) -> str:
        """Escape, then apply inline links and bold.

        Escaping runs first so script content can never inject markup; the
        link/bold substitutions insert the only tags allowed through.
        Link targets are matched pre-escape via placeholder indirection.
        """
        # Extract links before escaping so URLs survive intact.
        links: List[tuple[str, str]] = []

        def stash(match: "re.Match[str]") -> str:
            links.append((match.group(1), match.group(2)))
            return f"\x00{len(links) - 1}\x00"

        stashed = _LINK_RE.sub(stash, text)
        escaped = html.escape(stashed)
        escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
        for index, (label, target) in enumerate(links):
            anchor = f'<a href="{html.escape(target, quote=True)}" style="{_STYLE_LINK}">{html.escape(label)}</a>'
            escaped = escaped.replace(f"\x00{index}\x00", anchor)
        return escaped
