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

"""Render a lifecycle message into email parts (spec #118).

Same shape as the briefing email (spec #51): an inline-styled HTML part, a
plain-text alternative, absolute links built from ``PUBLIC_BASE_URL``, and
a signed one-click unsubscribe with the ``List-Unsubscribe`` / RFC 8058
headers. The unsubscribe link carries the product-tips purpose, so it turns
off tips only, never briefing emails.
"""

import html
from typing import Optional
from urllib.parse import quote

from ...models.user_message import LifecycleFacts
from ...utils.unsubscribe_token import TIPS_PURPOSE, make_unsubscribe_token
from ..email_markup import (
    STYLE_BODY,
    STYLE_BUTTON,
    STYLE_CARD,
    STYLE_FOOTER,
    STYLE_LINK,
    STYLE_P,
    RenderedEmail,
    absolutize_links,
    markdown_to_html,
    markdown_to_plain,
)
from .messages import LifecycleMessage


def _first_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    first = name.strip().split()[0] if name.strip() else ""
    return first or None


class LifecycleEmailRenderer:
    def __init__(self, *, public_base_url: str, secret: str) -> None:
        if not public_base_url:
            raise ValueError("PUBLIC_BASE_URL is required for lifecycle email")
        if not secret:
            raise ValueError("JWT_SECRET_KEY or UNSUBSCRIBE_SECRET is required to sign unsubscribe links")
        self._base_url = public_base_url.rstrip("/")
        self._secret = secret

    def unsubscribe_url(self, user_id: str) -> str:
        token = make_unsubscribe_token(user_id, self._secret, purpose=TIPS_PURPOSE)
        return f"{self._base_url}/unsubscribe/tips?token={quote(token, safe='')}"

    def render(self, message: LifecycleMessage, facts: LifecycleFacts) -> RenderedEmail:
        content = message.compose(facts)
        first = _first_name(facts.name)
        greeting = f"Hi {first}," if first else "Hi,"
        body = absolutize_links(content.body, self._base_url)
        app_url = f"{self._base_url}/inbox"
        settings_url = f"{self._base_url}/settings"
        unsubscribe_url = self.unsubscribe_url(facts.user_id)

        text = (
            f"{greeting}\n\n{markdown_to_plain(body).rstrip()}\n\n"
            f"Open thestill: {app_url}\n\n"
            "---\n"
            "You're getting this because Product tips by email is on in your thestill settings "
            f"({settings_url}).\n"
            f"Stop these emails: {unsubscribe_url}\n"
        )

        html_doc = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>\n'
            f'<body style="{STYLE_BODY}">\n'
            f'<div style="{STYLE_CARD}">\n'
            f'<p style="{STYLE_P}">{html.escape(greeting)}</p>\n'
            f"{markdown_to_html(body)}\n"
            f'<p><a href="{html.escape(app_url, quote=True)}" style="{STYLE_BUTTON}">Open thestill</a></p>\n'
            f'<div style="{STYLE_FOOTER}">\n'
            "<p>You're getting this because Product tips by email is on in your "
            f'<a href="{html.escape(settings_url, quote=True)}" style="{STYLE_LINK}">thestill settings</a>.</p>\n'
            f'<p><a href="{html.escape(unsubscribe_url, quote=True)}" style="{STYLE_LINK}">Unsubscribe</a> '
            "from tips with one click. Briefing emails are not affected.</p>\n"
            "</div>\n</div>\n</body>\n</html>\n"
        )

        headers = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            "Auto-Submitted": "auto-generated",
        }
        return RenderedEmail(subject=content.subject, html=html_doc, text=text, headers=headers)
