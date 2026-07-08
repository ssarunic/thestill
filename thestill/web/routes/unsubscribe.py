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
``email_enabled`` off. ``GET`` serves the human click from a mail client;
``POST`` serves RFC 8058 one-click unsubscribe (the ``List-Unsubscribe-Post``
header points mail providers here).

Both are idempotent, and a token for a user who never configured a
schedule confirms success too — the goal state ("no more emails") already
holds, and a distinct error would leak account state to token guessers.
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
</div>
</body>
</html>
"""


def _page(title: str, body: str, status_code: int) -> HTMLResponse:
    content = _PAGE_TEMPLATE.format(title=html.escape(title), body=html.escape(body))
    return HTMLResponse(content=content, status_code=status_code)


def _unsubscribe(token: str, app_state: AppState) -> HTMLResponse:
    user_id = verify_unsubscribe_token(token, app_state.config.jwt_secret_key)
    if user_id is None:
        return _page(
            "Link not valid",
            "This unsubscribe link is invalid or was truncated. "
            "You can also turn off briefing emails in the app under Settings.",
            status_code=400,
        )
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


@router.get("/unsubscribe/briefings")
async def unsubscribe_briefings(
    token: str = "",
    app_state: AppState = Depends(get_app_state),
) -> HTMLResponse:
    """Human click from a mail client: verify the token and flip the flag."""
    return _unsubscribe(token, app_state)


@router.post("/unsubscribe/briefings")
async def unsubscribe_briefings_one_click(
    token: str = "",
    app_state: AppState = Depends(get_app_state),
) -> HTMLResponse:
    """RFC 8058 one-click unsubscribe — mail providers POST to the same URL."""
    return _unsubscribe(token, app_state)
