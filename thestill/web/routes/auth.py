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
Authentication API routes for Thestill web server.

Supports both single-user and multi-user authentication modes:
- Single-user (MULTI_USER=false): Auto-creates default user, no login required
- Multi-user (MULTI_USER=true): Google OAuth authentication flow

Routes:
- GET /auth/status - Get auth configuration and current user
- GET /auth/google/login - Redirect to Google OAuth (multi-user only)
- GET /auth/google/callback - Handle OAuth callback (multi-user only)
- POST /auth/logout - Clear auth cookie
- GET /auth/me - Get current user info
"""

from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from structlog import get_logger

from ...models.user import PRIVATE_USER_FIELDS
from ...services.abuse.errors import AccountDisabledError, SignupRefusedError
from ...services.analytics_sink import LoginCancelled, LoginCompleted, LoginFailed, LoginStarted
from ...utils.page_kind import public_page_kind, signup_source_for
from ..analytics_request import capture_event, login_flow_key, may_measure
from ..dependencies import AppState, get_app_state
from ..middleware import AUTH_LIMIT, rate_limit_dependency, resolve_client_ip, trusted_proxy_set
from ..responses import api_response

logger = get_logger(__name__)

# Every /api/auth/* route is IP-rate-limited to blunt
# brute force against OAuth state and authenticated probes.
router = APIRouter(dependencies=[Depends(rate_limit_dependency(AUTH_LIMIT, "auth"))])

# Cookie configuration
AUTH_COOKIE_NAME = "auth_token"
AUTH_COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds


def _get_redirect_uri(request: Request, state: AppState) -> str:
    """
    Build the OAuth callback redirect URI.

    ``X-Forwarded-*`` headers are honoured only when (a) the immediate
    peer is in ``trusted_proxies`` AND (b) both ``X-Forwarded-Proto``
    and ``X-Forwarded-Host`` were actually forwarded. A trusted proxy
    that drops or forgets ``X-Forwarded-Host`` would otherwise let
    Starlette derive the callback host from the attacker-controllable
    ``Host`` header — refuse that path explicitly.

    When forwarded headers are incomplete, or the peer is untrusted,
    fall back to ``public_base_url``. If that is also unset, raise 500.
    """
    client_host = request.client.host if request.client else ""
    trusted = trusted_proxy_set(state.config)

    if client_host in trusted:
        fwd_proto = request.headers.get("X-Forwarded-Proto")
        fwd_host = request.headers.get("X-Forwarded-Host")
        if fwd_proto and fwd_host:
            return f"{fwd_proto}://{fwd_host}/api/auth/google/callback"
        # Trusted peer but no forwarded host. Do NOT read
        # request.url.netloc — it's Host-spoofable. Fall through to
        # the configured canonical URL.
        logger.warning(
            "oauth_trusted_proxy_missing_forwarded_headers",
            client_host=client_host,
            has_x_forwarded_proto=bool(fwd_proto),
            has_x_forwarded_host=bool(fwd_host),
        )

    if state.config.public_base_url:
        return f"{state.config.public_base_url}/api/auth/google/callback"

    logger.error(
        "oauth_redirect_fail_closed",
        reason="public_base_url_not_configured",
        client_host=client_host,
    )
    raise HTTPException(
        status_code=500,
        detail=("OAuth redirect is not configured on this server. " "Set PUBLIC_BASE_URL explicitly."),
    )


def _set_secure_cookie(
    response: Response,
    *,
    name: str,
    value: str,
    max_age: int,
    samesite: str,
    state: AppState,
) -> None:
    """Set an httponly, ``state.config.cookie_secure``-gated cookie.

    ``samesite=strict`` locks down the session bearer; OAuth's state cookie
    needs ``lax`` because it must survive the cross-site redirect back from
    Google.
    """
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        samesite=samesite,
        secure=state.config.cookie_secure,
    )


def _set_auth_cookie(response: Response, token: str, state: AppState) -> None:
    _set_secure_cookie(
        response,
        name=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_COOKIE_MAX_AGE,
        samesite="strict",
        state=state,
    )


def _clear_auth_cookie(response: Response) -> None:
    """Clear the authentication cookie."""
    response.delete_cookie(key=AUTH_COOKIE_NAME)


def _get_token_from_request(request: Request) -> Optional[str]:
    """Extract auth token from cookie or Authorization header."""
    # First try cookie
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        return token

    # Fall back to Authorization header (for API clients)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


@router.get("/status")
async def auth_status(request: Request, state: AppState = Depends(get_app_state)):
    """
    Get authentication status and configuration.

    Side-effect: when the resolved user has no region yet and has not
    explicitly locked one, attempts an IP-based geolocation and persists
    the result. Failures are silent — the user simply ends up with a
    null region and the UI falls back to its default.

    Returns:
        Auth mode configuration and current user if authenticated.
    """
    multi_user = state.config.multi_user

    # Get current user. Spec #104: a suspended or banned account reads as
    # signed out plus a flag, so the app shows /login's "account disabled"
    # message instead of looping between /login and a 403.
    token = _get_token_from_request(request)
    account_disabled = False
    try:
        user = state.auth_service.get_current_user(token)
    except AccountDisabledError:
        user, account_disabled = None, True

    if user is not None:
        await state.auth_service.maybe_infer_region(user, resolve_client_ip(request))

    sink = state.analytics_sink
    usage_events = bool(sink is not None and sink.enabled)

    return api_response(
        {
            "multi_user": multi_user,
            # Spec #94: whether signed-out visitors get the public site
            # (else the frontend keeps the sign-in wall).
            "public_pages": bool(multi_user and state.config.public_pages_enabled),
            "authenticated": user is not None,
            "account_disabled": account_disabled,
            # Spec #104: shown on /login for a disabled account or paused signups.
            "admin_contact_email": state.config.admin_contact_email or None,
            "user": user.model_dump(exclude=PRIVATE_USER_FIELDS) if user else None,
            # Spec #51 capability flag: the settings UI only shows the
            # briefing email-delivery checkbox when a provider is
            # configured AND the briefing scheduler is running — the
            # delivery pass lives on the scheduler tick, so without it an
            # opt-in would be accepted but nothing would ever send.
            "email_delivery_available": (
                state.briefing_delivery_service is not None and state.briefing_scheduler is not None
            ),
            # Spec #95: whether usage events exist at all (shows the Settings
            # switch), and the key the signed-in app initialises its SDK
            # with. No key for an opted-out user or under GPC, so the
            # browser never loads the analytics code for them.
            "usage_analytics_available": usage_events,
            "analytics": (
                {"key": sink.browser_key} if usage_events and user is not None and may_measure(user, request) else None
            ),
        }
    )


# Spec #94 — where to land after sign-in. A same-origin path only: starts with
# one "/", no scheme, no "//" or "/\\" (protocol-relative), no control
# characters, and never back to /login. Anything else lands on "/".
NEXT_COOKIE = "oauth_next"
_NEXT_MAX_LENGTH = 512


def safe_next_path(value: Optional[str]) -> Optional[str]:
    """``value`` if it is a safe same-origin path to return to, else None."""
    if not value or len(value) > _NEXT_MAX_LENGTH:
        return None
    if not value.startswith("/") or value.startswith(("//", "/\\")):
        return None
    if any(ord(ch) < 0x20 or ch == "\\" for ch in value):
        return None
    path = value.split("?", 1)[0].split("#", 1)[0]
    if path == "/login" or path.startswith("/login/") or path.startswith("/api/"):
        return None
    return value


@router.get("/google/login")
def google_login(
    request: Request,
    next: Optional[str] = Query(None, max_length=_NEXT_MAX_LENGTH),  # noqa: A002 - the query name is the contract
    state: AppState = Depends(get_app_state),
):
    """
    Initiate Google OAuth login flow.

    Redirects to Google's authorization page. Only available in multi-user mode.

    Returns:
        Redirect to Google OAuth authorization page.

    Raises:
        HTTPException: If not in multi-user mode.
    """
    if not state.config.multi_user:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth is not available in single-user mode",
        )

    redirect_uri = _get_redirect_uri(request, state)
    auth_url, state_token = state.auth_service.get_google_auth_url(redirect_uri)

    # Store state in session cookie for CSRF protection
    response = RedirectResponse(url=auth_url, status_code=302)
    _set_secure_cookie(
        response,
        name="oauth_state",
        value=state_token,
        max_age=600,  # 10 minutes
        samesite="lax",
        state=state,
    )
    # Spec #94 — remember where the visitor was; validated again on return.
    safe_next = safe_next_path(next)
    if safe_next:
        _set_secure_cookie(response, name=NEXT_COOKIE, value=safe_next, max_age=600, samesite="lax", state=state)
    else:
        # An abandoned earlier flow must not steer this sign-in.
        response.delete_cookie(key=NEXT_COOKIE)

    # Spec #95: which public page sign-in started from. Anonymous, keyed to
    # this attempt so the callback's login_completed joins it in a funnel.
    capture_event(
        state,
        request,
        LoginStarted(from_kind=public_page_kind(safe_next) or "none"),
        anonymous_key=login_flow_key(state.config.jwt_secret_key, state_token),
    )
    logger.info("Redirecting to Google OAuth")
    return response


# Google ``error`` values kept as they are on ``login_cancelled``; the rest
# are bucketed as ``other`` (see ``GoogleLoginError``).
_GOOGLE_ERRORS = frozenset({"access_denied", "admin_policy_enforced", "server_error", "temporarily_unavailable"})


def _google_error_reason(error: Optional[str]) -> str:
    if not error:
        return "missing_code"
    return error if error in _GOOGLE_ERRORS else "other"


def _back_to_login(notice: str, next_path: Optional[str]) -> RedirectResponse:
    """End a sign-in attempt on /login with a notice, keeping the return path.

    The attempt's state and return-path cookies are spent either way.
    """
    query = {"error": notice}
    if next_path:
        query["next"] = next_path
    response = RedirectResponse(url=f"/login?{urlencode(query)}", status_code=302)
    response.delete_cookie(key="oauth_state")
    response.delete_cookie(key=NEXT_COOKIE)
    return response


def _capture_attempt_end(app_state: AppState, request: Request, event, stored_state: Optional[str]) -> None:
    """Record how a sign-in attempt ended, under its ``login_started`` id.

    Keyed by this browser's own state cookie rather than the query: the
    cookie is the attempt this browser started. Without it there is no
    attempt to attribute the outcome to, so nothing is sent.
    """
    if stored_state:
        key = login_flow_key(app_state.config.jwt_secret_key, stored_state)
        capture_event(app_state, request, event, anonymous_key=key)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = Query(None, max_length=200),
    app_state: AppState = Depends(get_app_state),
):
    """
    Handle Google OAuth callback.

    Exchanges the authorization code for tokens, creates/updates the user,
    and sets the auth cookie. Every other outcome ends on /login with a
    notice and one usage event under the attempt's id: ``login_cancelled``
    when Google sends the reader back without a code (Cancel on its screen
    is ``access_denied``), ``login_failed`` when Google signed them in but
    thestill did not.

    Args:
        code: Authorization code from Google (absent when Google reports an error)
        state: State parameter for CSRF verification
        error: Google's OAuth error code, when it returns one instead of a code

    Returns:
        Redirect to where sign-in started with the auth cookie set, or to
        /login with ``error`` set to the notice to show.

    Raises:
        HTTPException: If not in multi-user mode.
    """
    if not app_state.config.multi_user:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth is not available in single-user mode",
        )

    stored_state = request.cookies.get("oauth_state")
    # Spec #94 return path, read once: it steers the redirect below and,
    # for a brand-new account, records which public page it came from
    # (spec #95; signup_source_for is total, so it cannot fail the login).
    next_path = safe_next_path(request.cookies.get(NEXT_COOKIE))

    # Google came back without a code: nothing to verify or exchange. Show
    # /login rather than a raw validation error.
    if error or not code:
        reason = _google_error_reason(error)
        logger.info("oauth_callback_cancelled", reason=reason)
        _capture_attempt_end(app_state, request, LoginCancelled(reason=reason), stored_state)
        return _back_to_login("cancelled" if reason == "access_denied" else "failed", next_path)

    # Verify state parameter for CSRF protection. No auth cookie is set on
    # this path, so a friendly redirect is as safe as a bare 400.
    if not stored_state or stored_state != state:
        logger.warning("OAuth state mismatch - possible CSRF attack")
        _capture_attempt_end(app_state, request, LoginFailed(reason="state_mismatch"), stored_state)
        return _back_to_login("failed", next_path)

    try:
        redirect_uri = _get_redirect_uri(request, app_state)
        user, jwt_token, is_new_user = await app_state.auth_service.handle_google_callback(
            code=code,
            redirect_uri=redirect_uri,
            signup_source=signup_source_for(next_path),
        )

        # Spec #64 — a brand-new real user claims the legacy single-user
        # local account's data (follows, inbox, briefings). Best-effort
        # and self-limiting: a successful claim deletes the local row, so
        # later logins no-op. Never blocks login (claim_for_new_user
        # swallows all errors).
        if is_new_user and app_state.legacy_claim_service is not None:
            app_state.legacy_claim_service.claim_for_new_user(user)

        # Best-effort region inference on first login. Failures are silent
        # and leave region NULL — the user can still pick one in settings.
        await app_state.auth_service.maybe_infer_region(user, resolve_client_ip(request))

        # Redirect back to where sign-in started (spec #94), else home.
        destination = next_path or "/"
        response = RedirectResponse(url=destination, status_code=302)
        _set_auth_cookie(response, jwt_token, app_state)

        # Clear the OAuth state and return-path cookies
        response.delete_cookie(key="oauth_state")
        response.delete_cookie(key=NEXT_COOKIE)

        # Spec #95: the same anonymous attempt id as login_started, never
        # linked to the account; skipped for a reader who opted out.
        capture_event(
            app_state,
            request,
            LoginCompleted(next_kind=public_page_kind(next_path) or "none"),
            anonymous_key=login_flow_key(app_state.config.jwt_secret_key, state),
            consent_of=user,
        )
        logger.info("user_authenticated", email=user.email)
        return response

    except (AccountDisabledError, SignupRefusedError) as e:
        # Spec #104 — not an auth failure: /login explains it.
        refusal = "account_disabled" if isinstance(e, AccountDisabledError) else "signups_closed"
        logger.info("oauth_callback_refused", error=refusal)
        _capture_attempt_end(app_state, request, LoginFailed(reason=refusal), stored_state)
        return _back_to_login(refusal, next_path)
    except Exception as e:
        # Don't leak upstream error messages to the client.
        # Log the type + message server-side; the reader gets a generic notice.
        logger.error("oauth_callback_error", error_type=type(e).__name__, error=str(e))
        _capture_attempt_end(app_state, request, LoginFailed(reason="error"), stored_state)
        return _back_to_login("failed", next_path)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    state: AppState = Depends(get_app_state),
):
    """Log out the current user.

    Clears the auth cookie *and* revokes the token's ``jti`` server-side
    (spec #25 item 4.2) so it can't be reused even if the client keeps
    a copy. A token without a jti (legacy) silently no-ops on revoke;
    the client cookie still clears so the user is logged out either way.
    """
    token = _get_token_from_request(request)
    revoked = False
    if token:
        revoked = state.auth_service.revoke_token(token)
    _clear_auth_cookie(response)
    logger.info("user_logged_out", token_revoked=revoked)
    return api_response({"message": "Logged out successfully"})


@router.get("/me")
def get_current_user(request: Request, state: AppState = Depends(get_app_state)):
    """
    Get the current authenticated user.

    In single-user mode, returns the default user.
    In multi-user mode, returns the user from the JWT token.

    Returns:
        Current user info.

    Raises:
        HTTPException: If not authenticated in multi-user mode.
    """
    token = _get_token_from_request(request)
    user = state.auth_service.get_current_user(token)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    return api_response(
        {
            "user": user.model_dump(exclude=PRIVATE_USER_FIELDS),
        }
    )


class UpdateMeRequest(BaseModel):
    """Body for PATCH /api/auth/me. Only the fields sent are changed."""

    region: Optional[str] = Field(
        default=None,
        description="ISO 3166-1 alpha-2 country code (case-insensitive). "
        "Pass null to clear; either way the locked flag is set so future "
        "logins won't overwrite the choice.",
    )
    usage_analytics_enabled: Optional[bool] = Field(
        default=None,
        description="Spec #95: the Settings switch for usage events.",
    )


@router.patch("/me")
def update_current_user(
    payload: UpdateMeRequest,
    request: Request,
    state: AppState = Depends(get_app_state),
):
    """Update the current user's profile; only the fields sent change.

    Setting ``region`` (even to null) locks future IP-based inference; the
    only way to re-enable inference is to clear region in the database.
    ``usage_analytics_enabled`` is the spec #95 usage-events switch.
    """
    token = _get_token_from_request(request)
    user = state.auth_service.get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    updated = user
    if "region" in payload.model_fields_set:
        try:
            updated = state.auth_service.set_user_region(updated, payload.region)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.usage_analytics_enabled is not None:
        updated = state.auth_service.set_usage_analytics(updated, payload.usage_analytics_enabled)

    return api_response({"user": updated.model_dump(exclude=PRIVATE_USER_FIELDS)})
