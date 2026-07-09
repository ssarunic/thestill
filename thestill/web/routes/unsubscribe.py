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
One-click briefing email unsubscribe (spec #51).

Unauthenticated by design: the link lives in an email and must work
without login (CAN-SPAM/GDPR). Authorization comes from the HMAC-signed
token instead — it grants exactly one capability, flipping the bearer's
``email_enabled`` off. ``GET`` serves the human click from a mail client
and renders a confirmation page whose button POSTs back; only ``POST``
mutates. Mail security gateways (SafeLinks, Proofpoint) and link-preview
bots prefetch every GET in an email body, so a mutating GET would
silently unsubscribe users who never clicked — the state change lives
exclusively on the POST, which is also what RFC 8058 one-click providers
call (the ``List-Unsubscribe-Post`` header points them here).

The POST is idempotent, and a token for a user who never configured a
schedule confirms success too — the goal state ("no more emails") already
holds, and a distinct error would leak account state to token guessers.

Handlers are plain ``def`` so FastAPI runs the synchronous repository
write on its threadpool instead of the event loop.
"""

import html

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from structlog import get_logger

from ...utils.unsubscribe_token import verify_unsubscribe_token
from ..dependencies import AppState, get_app_state

logger = get_logger(__name__)

router = APIRouter()

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{title}</title></head>
<body style="font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
             background:#f4f4f5;margin:0;padding:40px 16px;">
<div style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:8px;padding:32px;
            font-size:15px;line-height:1.6;color:#18181b;">
<h1 style="font-size:20px;margin:0 0 12px;">{title}</h1>
<p style="margin:0;">{body}</p>
{extra}
</div>
</body>
</html>
"""

_CONFIRM_FORM_TEMPLATE = (
    '<form method="post" action="/unsubscribe/briefings?token={token}" style="margin:20px 0 0;">'
    '<button type="submit" style="padding:10px 18px;background:#18181b;color:#ffffff;'
    'border:none;border-radius:6px;font-size:15px;font-weight:600;cursor:pointer;">'
    "Unsubscribe</button></form>"
)


def _page(title: str, body: str, status_code: int, extra_html: str = "") -> HTMLResponse:
    content = _PAGE_TEMPLATE.format(title=html.escape(title), body=html.escape(body), extra=extra_html)
    return HTMLResponse(content=content, status_code=status_code)


def _invalid_link_page() -> HTMLResponse:
    return _page(
        "Link not valid",
        "This unsubscribe link is invalid or was truncated. "
        "You can also turn off briefing emails in the app under Settings.",
        status_code=400,
    )


def _verify(token: str, app_state: AppState):
    config = app_state.config
    secret = config.unsubscribe_secret or config.jwt_secret_key
    return verify_unsubscribe_token(token, secret)


@router.get("/unsubscribe/briefings")
def unsubscribe_briefings(
    token: str = "",
    app_state: AppState = Depends(get_app_state),
) -> HTMLResponse:
    """Human click from a mail client: confirm before mutating.

    Read-only by design — gateways prefetch GETs. The page's button POSTs
    the same token back, and only the POST flips the flag.
    """
    if _verify(token, app_state) is None:
        return _invalid_link_page()
    return _page(
        "Unsubscribe from briefing emails?",
        "Click the button to stop receiving briefing emails. "
        "Your briefings keep generating on schedule — you can always read "
        "them in the app, or re-enable email any time in Settings.",
        status_code=200,
        extra_html=_CONFIRM_FORM_TEMPLATE.format(token=html.escape(token, quote=True)),
    )


@router.post("/unsubscribe/briefings")
def unsubscribe_briefings_one_click(
    token: str = "",
    app_state: AppState = Depends(get_app_state),
) -> HTMLResponse:
    """Perform the unsubscribe: confirm-page button and RFC 8058 one-click."""
    user_id = _verify(token, app_state)
    if user_id is None:
        return _invalid_link_page()
    if app_state.briefing_schedule_repository is not None:
        updated = app_state.briefing_schedule_repository.set_email_enabled(user_id, False)
        if updated:
            logger.info("briefing_email_unsubscribed", user_id=user_id)
    return _page(
        "You're unsubscribed",
        "Briefing emails are switched off for your account. "
        "Your briefings keep generating on schedule — read them in the app, "
        "or re-enable email any time in Settings.",
        status_code=200,
    )
