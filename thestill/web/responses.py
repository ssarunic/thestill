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
Response helpers for Thestill web API.

Provides standardized response formatting to ensure consistent API responses
across all endpoints, reducing code duplication (DRY principle).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, NoReturn, Optional

from fastapi import HTTPException


def api_response(data: Dict[str, Any], status: str = "ok") -> Dict[str, Any]:
    """
    Wrap data in standard API response envelope.

    All API responses include:
    - status: Operation status (default "ok")
    - timestamp: ISO-formatted UTC timestamp

    Args:
        data: Response data to include
        status: Status string (default "ok")

    Returns:
        Dict with status, timestamp, and spread data fields

    Example:
        >>> api_response({"user": "alice"})
        {"status": "ok", "timestamp": "2026-01-13T...", "user": "alice"}
    """
    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data,
    }


def paginated_response(
    items: List[Any],
    total: int,
    offset: int,
    limit: int,
    items_key: str = "items",
) -> Dict[str, Any]:
    """
    Create paginated API response with standard pagination fields.

    Args:
        items: List of items for current page
        total: Total number of items across all pages
        offset: Current offset (number of items skipped)
        limit: Maximum items per page
        items_key: Key name for items list (default "items")

    Returns:
        Dict with pagination metadata and items

    Example:
        >>> paginated_response([{"id": 1}], total=10, offset=0, limit=5, items_key="users")
        {
            "status": "ok",
            "timestamp": "...",
            "users": [{"id": 1}],
            "count": 1,
            "total": 10,
            "offset": 0,
            "limit": 5,
            "has_more": True,
            "next_offset": 5
        }
    """
    has_more = offset + len(items) < total
    return api_response(
        {
            items_key: items,
            "count": len(items),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
        }
    )


# =============================================================================
# HTTP Error Helpers
# =============================================================================


def not_found(resource: str, identifier: str) -> NoReturn:
    """
    Raise 404 Not Found for a resource.

    Args:
        resource: Type of resource (e.g., "Podcast", "Episode")
        identifier: Resource identifier that was not found

    Raises:
        HTTPException: Always raises 404

    Example:
        >>> not_found("Podcast", "my-podcast-slug")
        # Raises HTTPException(status_code=404, detail="Podcast not found: my-podcast-slug")
    """
    raise HTTPException(status_code=404, detail=f"{resource} not found: {identifier}")


def bad_request(message: str) -> NoReturn:
    """
    Raise 400 Bad Request.

    Args:
        message: Error message describing the problem

    Raises:
        HTTPException: Always raises 400

    Example:
        >>> bad_request("Invalid date format")
        # Raises HTTPException(status_code=400, detail="Invalid date format")
    """
    raise HTTPException(status_code=400, detail=message)


def parse_iso_datetime(value: Optional[str], *, field_name: str) -> Optional[datetime]:
    """
    Parse a query-string ISO-8601 timestamp or raise 400.

    ``Z`` suffixes are normalized to ``+00:00`` so RFC 3339 inputs accepted by
    most clients still parse with stdlib ``fromisoformat``. Returns ``None``
    when ``value`` is empty so callers can pass through optional cursors
    without an extra null-check.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} format: {value}")


def conflict(message: str) -> NoReturn:
    """
    Raise 409 Conflict.

    Args:
        message: Error message describing the conflict

    Raises:
        HTTPException: Always raises 409

    Example:
        >>> conflict("Task already running")
        # Raises HTTPException(status_code=409, detail="Task already running")
    """
    raise HTTPException(status_code=409, detail=message)


def etag_json_response(request: "Request", data: Dict[str, Any], status: str = "ok") -> "Response":
    """``api_response`` with a weak content-hash ETag (spec #69 Phase 6).

    For write-once resources (transcripts, word timestamps, summaries,
    narration scripts): the ETag is a sha1 over the serialized ``data``
    payload, deliberately excluding the envelope's per-request
    ``timestamp`` so an unchanged resource still revalidates.

    That exclusion is why the validator is **weak** (``W/`` prefix). The
    envelope stamps a fresh ``timestamp`` on every response, so two bodies
    carrying the same payload are not byte-identical; a strong validator
    asserts byte equality (RFC 9110 §8.8.1) and would be lying. Weak is
    the accurate claim: semantically equivalent, not octet-equal. Nothing
    here serves ranges, which is the case strong validators buy you.

    ``Cache-Control: private, no-cache`` forces revalidation on every use
    (these artifacts CAN be regenerated), but a matching ``If-None-Match``
    answers 304 with no body — the entire transfer for a multi-hundred-KB
    transcript payload.
    """
    import hashlib
    import json as _json

    from fastapi import Request, Response  # noqa: F401  (Request used in signature)
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    encoded = jsonable_encoder(data)
    digest = hashlib.sha1(  # noqa: S324 — cache validator, not crypto
        _json.dumps(encoded, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    etag = f'W/"{digest}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}

    # Weak comparison (RFC 9110 §8.8.3.2): ignore the ``W/`` prefix on both
    # sides so a client that echoes the opaque tag in either form still
    # revalidates. ``*`` matches any current representation.
    def _opaque(tag: str) -> str:
        return tag.strip().removeprefix("W/").strip()

    if_none_match = request.headers.get("if-none-match", "")
    candidates = {_opaque(tag) for tag in if_none_match.split(",")}
    if "*" in candidates or _opaque(etag) in candidates:
        return Response(status_code=304, headers=headers)

    return JSONResponse(content=api_response(encoded, status=status), headers=headers)
