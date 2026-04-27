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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from structlog import get_logger

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

    # Get current user
    token = _get_token_from_request(request)
    user = state.auth_service.get_current_user(token)

    if user is not None:
        await state.auth_service.maybe_infer_region(user, resolve_client_ip(request))

    return api_response(
        {
            "multi_user": multi_user,
            "authenticated": user is not None,
            "user": user.model_dump(exclude={"google_id"}) if user else None,
        }
    )


@router.get("/google/login")
async def google_login(request: Request, state: AppState = Depends(get_app_state)):
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

    logger.info("Redirecting to Google OAuth")
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str,
    state: str,
    app_state: AppState = Depends(get_app_state),
):
    """
    Handle Google OAuth callback.

    Exchanges the authorization code for tokens, creates/updates the user,
    and sets the auth cookie.

    Args:
        code: Authorization code from Google
        state: State parameter for CSRF verification

    Returns:
        Redirect to home page with auth cookie set.

    Raises:
        HTTPException: If state mismatch or OAuth error.
    """
    if not app_state.config.multi_user:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth is not available in single-user mode",
        )

    # Verify state parameter for CSRF protection
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or stored_state != state:
        logger.warning("OAuth state mismatch - possible CSRF attack")
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    try:
        redirect_uri = _get_redirect_uri(request, app_state)
        user, jwt_token = await app_state.auth_service.handle_google_callback(
            code=code,
            redirect_uri=redirect_uri,
        )

        # Best-effort region inference on first login. Failures are silent
        # and leave region NULL — the user can still pick one in settings.
        await app_state.auth_service.maybe_infer_region(user, resolve_client_ip(request))

        # Redirect to home page with auth cookie
        response = RedirectResponse(url="/", status_code=302)
        _set_auth_cookie(response, jwt_token, app_state)

        # Clear the OAuth state cookie
        response.delete_cookie(key="oauth_state")

        logger.info("user_authenticated", email=user.email)
        return response

    except Exception as e:
        # Don't leak upstream error messages to the client.
        # Log the type + message server-side; respond with a generic message.
        logger.error("oauth_callback_error", error_type=type(e).__name__, error=str(e))
        raise HTTPException(status_code=400, detail="Authentication failed") from e


@router.post("/logout")
async def logout(
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
async def get_current_user(request: Request, state: AppState = Depends(get_app_state)):
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
            "user": user.model_dump(exclude={"google_id"}),
        }
    )


class UpdateMeRequest(BaseModel):
    """Body for PATCH /api/auth/me. Only ``region`` is mutable today."""

    region: Optional[str] = Field(
        default=None,
        description="ISO 3166-1 alpha-2 country code (case-insensitive). "
        "Pass null to clear; either way the locked flag is set so future "
        "logins won't overwrite the choice.",
    )


@router.patch("/me")
async def update_current_user(
    payload: UpdateMeRequest,
    request: Request,
    state: AppState = Depends(get_app_state),
):
    """Update the current user's profile.

    Currently only ``region`` is mutable. Setting it always locks future
    IP-based inference; the only way to re-enable inference is to clear
    region directly in the database.
    """
    token = _get_token_from_request(request)
    user = state.auth_service.get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        updated = state.auth_service.set_user_region(user, payload.region)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return api_response({"user": updated.model_dump(exclude={"google_id"})})
