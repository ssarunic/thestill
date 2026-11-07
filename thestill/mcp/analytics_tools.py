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

"""Admin analytics tool for the MCP server (spec #115).

- ``get_analytics(range?)`` — the twin of ``GET /api/dashboard/analytics``:
  the spec #95 Layer A report (Scorecard, signups, activation, cohorts,
  engagement, content) plus the spec #115 public traffic section, from
  ``AnalyticsService.report()``.

Admins only (D1). The tool sits in the ``read`` scope, because ``read`` is
what the caller's web session can see and an admin's session can see
``/analytics``; the admin check is made here on every call, against the
admin status the transport resolves per request, so a demoted admin loses it
on the next call. Non-admins don't see it listed and get the same answer as
for a tool that doesn't exist.
"""

from __future__ import annotations

import json
from typing import Any, List

from mcp.types import TextContent, Tool

from ..services.analytics_service import ALLOWED_RANGES, AnalyticsService
from ..web.public_access import TtlCache
from .identity import McpIdentity

ANALYTICS_TOOL_NAMES = frozenset({"get_analytics"})

DEFAULT_TOOL_RANGE = "7d"
DETAIL_LEVELS = ("summary", "full")

# The report runs about a dozen statements; the web route caches it for the
# same 10 minutes.
CACHE_TTL_SECONDS = 600.0

# What ``detail=summary`` keeps of each ranked list (D7).
SUMMARY_ROWS = 10
SUMMARY_COHORT_ROWS = 6


def new_analytics_cache() -> TtlCache:
    """One per MCP server, keyed by range."""
    return TtlCache(CACHE_TTL_SECONDS, max_size=len(ALLOWED_RANGES))


def analytics_tool_definitions(identity: McpIdentity) -> List[Tool]:
    """The admin analytics tool, or nothing for a caller who isn't an admin."""
    if not identity.is_admin:
        return []
    return [
        Tool(
            name="get_analytics",
            description=(
                "Admins only. Thestill's product analytics from the production database, the same report "
                "as the /analytics page: the Scorecard (activation, engagement, week-4 retention, weekly "
                "return), signups per day by source, the activation funnel, retention by signup week, "
                "engagement (active readers, reads, briefings, follows), the most-read shows and episodes, "
                "and public traffic: signed-out page views per day by page kind and the most-viewed public "
                "pages with paths. Admin and test accounts are excluded. Not included: the sign-in funnel "
                "and referrers (PostHog) and search clicks (Search Console). Public views come from a "
                "per-page daily counter that also counts JS-running scrapers; a burst of entity views is "
                "usually one. Days are UTC and today is partial."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "range": {
                        "type": "string",
                        "enum": list(ALLOWED_RANGES),
                        "description": f"Window ending now (default {DEFAULT_TOOL_RANGE})",
                    },
                    "detail": {
                        "type": "string",
                        "enum": list(DETAIL_LEVELS),
                        "description": (
                            f"summary (default): top {SUMMARY_ROWS} of each ranked list, the last "
                            f"{SUMMARY_COHORT_ROWS} signup cohorts, activation overall only. full: every row."
                        ),
                    },
                },
            },
        )
    ]


def dispatch_analytics_tool(
    name: str, arguments: Any, identity: McpIdentity, *, service: AnalyticsService, cache: TtlCache
) -> List[TextContent]:
    """Run ``get_analytics`` for an admin; anyone else gets "Unknown tool"."""
    if not identity.is_admin:
        return _json({"success": False, "error": f"Unknown tool: {name}"})
    arguments = arguments or {}
    range_ = arguments.get("range") or DEFAULT_TOOL_RANGE
    detail = arguments.get("detail") or "summary"
    if range_ not in ALLOWED_RANGES or detail not in DETAIL_LEVELS:
        return _json(
            {
                "success": False,
                "error": f"range must be one of {', '.join(ALLOWED_RANGES)}; detail one of {', '.join(DETAIL_LEVELS)}",
            }
        )
    if not service.available:
        # The web route answers {"available": false}; the connector also
        # says why, because the model has to explain it (D3).
        return _json(
            {
                "available": False,
                "reason": "Product analytics needs multi-user mode on the Postgres backend.",
            }
        )
    report = cache.get_or_compute(range_, lambda: service.report(range_).model_dump(mode="json"))
    payload = {"available": True, "detail": detail, **report}
    return _json(payload if detail == "full" else _summary(payload))


def _summary(payload: dict) -> dict:
    """The report trimmed for a model to read: ranked lists cut to their
    top rows, recent cohorts, the activation funnel without the per-source
    split. Totals and notes are untouched, so nothing reads differently."""
    out = dict(payload)
    out["activation"] = {**payload["activation"], "by_source": []}
    out["cohorts"] = {**payload["cohorts"], "rows": payload["cohorts"]["rows"][-SUMMARY_COHORT_ROWS:]}
    content = payload["content"]
    out["content"] = {
        **content,
        "podcasts": content["podcasts"][:SUMMARY_ROWS],
        "episodes": content["episodes"][:SUMMARY_ROWS],
    }
    out["traffic"] = {**payload["traffic"], "top_pages": payload["traffic"]["top_pages"][:SUMMARY_ROWS]}
    out["trimmed"] = (
        f"detail=summary: top {SUMMARY_ROWS} rows per ranked list, last {SUMMARY_COHORT_ROWS} cohorts, "
        "no per-source activation. Pass detail=full for everything."
    )
    return out


def _json(payload: dict) -> List[TextContent]:
    # Compact: the full report is tens of kilobytes, and indentation would
    # add a third to what the model has to read.
    return [TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))]
