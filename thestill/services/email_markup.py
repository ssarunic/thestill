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
Shared email markup (spec #51, reused by spec #118).

The inline styles, the composed-email shape and a small markdown → HTML
pass over the constructs thestill's emails use (headings, links, bold,
list items, horizontal rules, paragraphs). The project has no general
markdown dependency and email must render deterministically, so unknown
constructs degrade to escaped paragraph text, never to broken markup.
"""

import html
import re
from dataclasses import dataclass, field
from typing import Dict, List

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")

STYLE_BODY = "margin:0;padding:0;background-color:#f4f4f5;"
STYLE_CARD = (
    "max-width:600px;margin:0 auto;padding:24px;background-color:#ffffff;"
    "font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    "font-size:15px;line-height:1.6;color:#18181b;"
)
STYLE_H1 = "font-size:22px;line-height:1.3;margin:0 0 12px;"
STYLE_H2 = "font-size:18px;line-height:1.3;margin:24px 0 8px;"
STYLE_H3 = "font-size:16px;line-height:1.3;margin:16px 0 4px;"
STYLE_P = "margin:8px 0;"
STYLE_HR = "border:none;border-top:1px solid #e4e4e7;margin:20px 0;"
STYLE_LINK = "color:#1d4ed8;text-decoration:underline;"
STYLE_BUTTON = (
    "display:inline-block;padding:10px 18px;margin:16px 0;background-color:#18181b;"
    "color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600;"
)
STYLE_FOOTER = "margin-top:28px;padding-top:16px;border-top:1px solid #e4e4e7;font-size:12px;color:#71717a;"


@dataclass
class RenderedEmail:
    """The composed parts the delivery pass hands to ``EmailSender``."""

    subject: str
    html: str
    text: str
    headers: Dict[str, str] = field(default_factory=dict)


def absolutize_links(markdown: str, base_url: str) -> str:
    """Rewrite app-relative link targets (``/podcasts/…``) to absolute."""

    def rewrite(match: "re.Match[str]") -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith("/"):
            target = f"{base_url}{target}"
        return f"[{label}]({target})"

    return LINK_RE.sub(rewrite, markdown)


def markdown_to_html(markdown: str) -> str:
    """Convert the email markdown subset to inline-styled HTML."""
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
            blocks.append(f'<hr style="{STYLE_HR}">')
        elif line.startswith("### "):
            flush_list()
            blocks.append(f'<h3 style="{STYLE_H3}">{inline_html(line[4:])}</h3>')
        elif line.startswith("## "):
            flush_list()
            blocks.append(f'<h2 style="{STYLE_H2}">{inline_html(line[3:])}</h2>')
        elif line.startswith("# "):
            flush_list()
            blocks.append(f'<h1 style="{STYLE_H1}">{inline_html(line[2:])}</h1>')
        elif line.startswith("- "):
            list_items.append(f"<li>{inline_html(line[2:])}</li>")
        else:
            flush_list()
            blocks.append(f'<p style="{STYLE_P}">{inline_html(line)}</p>')
    flush_list()
    return "\n".join(blocks)


def inline_html(text: str) -> str:
    """Escape, then apply inline links and bold.

    Escaping runs first so content can never inject markup; the link/bold
    substitutions insert the only tags allowed through. Link targets are
    matched pre-escape via placeholder indirection.
    """
    links: List[tuple[str, str]] = []

    def stash(match: "re.Match[str]") -> str:
        links.append((match.group(1), match.group(2)))
        return f"\x00{len(links) - 1}\x00"

    stashed = LINK_RE.sub(stash, text)
    escaped = html.escape(stashed)
    escaped = BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    for index, (label, target) in enumerate(links):
        anchor = f'<a href="{html.escape(target, quote=True)}" style="{STYLE_LINK}">{html.escape(label)}</a>'
        escaped = escaped.replace(f"\x00{index}\x00", anchor)
    return escaped


def markdown_to_plain(markdown: str) -> str:
    """The plain-text alternative: ``[label](url)`` → ``label (url)`` and
    bold markers dropped, so text-only mail clients show no markdown."""
    text = LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", markdown)
    return BOLD_RE.sub(r"\1", text)
