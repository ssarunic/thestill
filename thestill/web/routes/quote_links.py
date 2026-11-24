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

"""Quote short links (spec #114 Phase 0): ``/q/<id>`` and its card.

``GET /q/<id>`` is a small HTML page whose Open Graph tags *are* the quote
card, so pasting the link anywhere previews as the card. It then sends a
browser on to the episode page at that moment. The same bytes go to every
user agent: previewers read the tags, browsers follow the refresh.

- ``og:url`` is the short link itself and the canonical is the episode.
  Facebook and LinkedIn re-fetch ``og:url`` and use *its* tags, so pointing
  it at the episode would swap the card for the episode artwork (D1).
- The page is ``noindex`` (meta and header). It is not disallowed in
  robots.txt: Twitterbot obeys robots.txt, and a disallowed link would
  preview as nothing.
- Opens are counted for browsers only, per day and referrer host.

Signed out by design, on the public rate limit. Mounted only where the
public pages are.
"""

import html
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from structlog import get_logger

from ...services.quote_share_service import QuoteShare, QuoteShareService, ShareGone
from ...utils.user_agent import classify_user_agent
from ..dependencies import AppState, get_app_state
from ..public_access import public_rate_limit
from .api_public import _external_referrer_host

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(public_rate_limit)])

_CARD_CACHE_CONTROL = "public, max-age=86400"
_TITLE_MAX = 110

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="robots" content="noindex">
<link rel="canonical" href="{episode_url}">
<meta property="og:site_name" content="Thestill">
<meta property="og:type" content="article">
<meta property="og:url" content="{short_url}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:image" content="{image_url}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{image_url}">
<meta http-equiv="refresh" content="0;url={landing_url}">
</head>
<body style="font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;margin:0;padding:40px 16px;
             background:#f8f6f0;color:#18233a;">
<div style="max-width:560px;margin:0 auto;font-size:17px;line-height:1.6;">
<blockquote style="margin:0 0 12px;font-family:Georgia,serif;font-size:21px;">{quote}</blockquote>
<p style="margin:0 0 20px;color:#52607a;">{description}</p>
<p style="margin:0;"><a href="{landing_url}" style="color:#18233a;font-weight:600;">Open the episode at this moment</a></p>
</div>
</body>
</html>
"""


def _service(state: AppState) -> QuoteShareService:
    service = getattr(state, "quote_share_service", None)
    if service is None:
        raise HTTPException(status_code=404, detail="Quote link not found")
    return service


def _resolve(state: AppState, share_id: str) -> QuoteShare:
    try:
        share = _service(state).resolve(share_id)
    except ShareGone:
        raise HTTPException(status_code=410, detail="Quote link withdrawn") from None
    if share is None:
        raise HTTPException(status_code=404, detail="Quote link not found")
    return share


def _base_url(state: AppState, request: Request) -> str:
    return state.config.public_base_url or str(request.base_url).rstrip("/")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@router.get("/q/{share_id}.png")
def get_quote_card(share_id: str, request: Request, state: AppState = Depends(get_app_state)) -> Response:
    """The quote's link-preview card (1200 × 630 PNG), cached after the
    first render. 503 while rendering is switched off and it is not cached."""
    share = _resolve(state, share_id)
    service = _service(state)
    etag = f'"{share.id}-{service.card_etag(share)}"'
    headers = {"Cache-Control": _CARD_CACHE_CONTROL, "ETag": etag, "X-Robots-Tag": "noindex"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    png = service.card_png(share)
    if png is None:
        raise HTTPException(status_code=503, detail="Quote cards are paused", headers={"Retry-After": "3600"})
    return Response(content=png, media_type="image/png", headers=headers)


@router.get("/q/{share_id}", response_class=HTMLResponse)
def get_quote_link(share_id: str, request: Request, state: AppState = Depends(get_app_state)) -> HTMLResponse:
    """The short link: Open Graph tags for previewers, a refresh for browsers."""
    share = _resolve(state, share_id)
    base = _base_url(state, request)
    speaker = share.speaker_name or "A guest"
    description = f"{speaker} on {share.podcast_title}: {share.episode_title}"

    ua_class = classify_user_agent(request.headers.get("user-agent"))
    referrer: Optional[str] = _external_referrer_host(
        request.headers.get("referer"), request, state.config.public_base_url
    )
    if ua_class == "browser":
        _service(state).record_open(share, referrer or "")
    logger.info("quote_link_open", share_id=share.id, ua_class=ua_class, referrer_host=referrer)

    def esc(value: str) -> str:
        return html.escape(value, quote=True)

    page = _PAGE.format(
        title=esc(_clip(f"“{share.text}”", _TITLE_MAX)),
        description=esc(_clip(description, 200)),
        quote=esc(f"“{share.text}”"),
        short_url=esc(f"{base}/q/{share.id}"),
        episode_url=esc(f"{base}{share.episode_path}"),
        landing_url=esc(f"{base}{share.landing_path}"),
        image_url=esc(f"{base}/q/{share.id}.png"),
    )
    # Each open is counted, so the page itself is never cached.
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
