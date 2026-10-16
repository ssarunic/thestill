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

"""Server-side product usage events (spec #95 Layer B, the write side).

Every event the server may send is a model below; nothing else can be
captured, and unknown props are a validation error at the call site
(FM-7). The read side (Layer A, product tables) is ``AnalyticsService``;
the two never mix.

``capture`` is a queue push into the PostHog SDK's background thread: it
never blocks a request, never raises, and drops the event when the user
opted out or sent Global Privacy Control. Anonymous events carry a
daily-rotating visitor key (or nothing) and never create a person.
"""

import uuid
from typing import Literal, Optional, Protocol, Union

from pydantic import BaseModel, ConfigDict, Field
from structlog import get_logger

from ..models.user import User

logger = get_logger(__name__)

PublicKind = Literal["landing", "top", "podcast", "episode", "entity", "share", "legal"]
PageKindOrNone = Literal["landing", "top", "podcast", "episode", "entity", "share", "legal", "none"]


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def properties(self) -> dict:
        return self.model_dump(exclude={"name"})


class PublicPageView(_Event):
    """A signed-out view of a spec #94 public page (browsers only)."""

    name: Literal["public_page_view"] = "public_page_view"
    kind: PublicKind
    referrer_host: Optional[str] = Field(default=None, max_length=200)


class LoginStarted(_Event):
    name: Literal["login_started"] = "login_started"
    from_kind: PageKindOrNone


class LoginCompleted(_Event):
    name: Literal["login_completed"] = "login_completed"
    next_kind: PageKindOrNone


class Search(_Event):
    """A corpus search; never the query text."""

    name: Literal["search"] = "search"
    result_count: int = Field(ge=0)
    mode: Literal["lexical", "semantic", "hybrid"]


class McpToolCall(_Event):
    name: Literal["mcp_tool_call"] = "mcp_tool_call"
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ok: bool


AnalyticsEvent = Union[PublicPageView, LoginStarted, LoginCompleted, Search, McpToolCall]


class AnalyticsSink(Protocol):
    """Where server events go. ``browser_key`` is what the signed-in app
    initialises its SDK with (None when usage events are off)."""

    enabled: bool
    browser_key: Optional[str]

    def capture(
        self,
        event: AnalyticsEvent,
        *,
        user: Optional[User] = None,
        visitor_key: Optional[str] = None,
        gpc: bool = False,
    ) -> None: ...

    def shutdown(self) -> None: ...


class NullSink:
    """Usage events off: single-user mode, no key, or ANALYTICS_ENABLED=false."""

    enabled = False
    browser_key = None

    def capture(self, event, *, user=None, visitor_key=None, gpc=False) -> None:
        return None

    def shutdown(self) -> None:
        return None


class PostHogSink:
    """Sends events to PostHog through the SDK's bounded background queue."""

    enabled = True

    def __init__(self, project_key: str, host: str, *, client=None):
        """
        Args:
            project_key: the public ``phc_…`` key (also handed to the browser).
            host: PostHog ingestion host for server-side sends.
            client: injectable for tests; defaults to ``posthog.Posthog``.
        """
        if client is None:
            from posthog import Posthog

            client = Posthog(
                project_key,
                host=host,
                disable_geoip=True,
                enable_exception_autocapture=False,
                max_queue_size=5000,
            )
        self._client = client
        self.browser_key = project_key

    def capture(
        self,
        event: AnalyticsEvent,
        *,
        user: Optional[User] = None,
        visitor_key: Optional[str] = None,
        gpc: bool = False,
    ) -> None:
        try:
            properties = event.properties()
            if user is not None:
                # The per-user switch and GPC are the server's truth, not the
                # client's: an identified event is dropped for either.
                if gpc or not user.usage_analytics_enabled:
                    return
                distinct_id = user.id
                properties["$set"] = {"is_admin": user.is_admin}
            else:
                # Anonymous: a visitor key good for today only, or a fresh id
                # per event (GPC), and never a person profile.
                distinct_id = visitor_key or str(uuid.uuid4())
                properties["$process_person_profile"] = False
            self._client.capture(event.name, distinct_id=distinct_id, properties=properties)
        except Exception as exc:  # noqa: BLE001 - analytics never breaks the request it describes
            logger.warning("analytics_capture_failed", analytics_event=event.name, error=str(exc))

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception as exc:  # noqa: BLE001 - best effort at process exit
            logger.warning("analytics_shutdown_failed", error=str(exc))


def make_analytics_sink(config) -> Union[PostHogSink, NullSink]:
    """The sink for this process: PostHog only in multi-user mode with a key
    and the kill switch on. Logged once so a missing key shows in prod logs."""
    if config.multi_user and config.analytics_events_enabled and config.posthog_project_key:
        sink: Union[PostHogSink, NullSink] = PostHogSink(config.posthog_project_key, config.posthog_host)
    else:
        sink = NullSink()
    logger.info("analytics_configured", sink="posthog" if sink.enabled else "null")
    return sink
