"""Exception text never reaches an MCP client (``thestill/mcp/errors.py``).

The leak this pins: a database error raised inside a tool was relayed
verbatim by the dispatcher's catch-all — SQL parameter values and all — to
whoever held a connector token. Authored messages ("Podcast not found: …",
scope refusals) must still get through, or the model cannot react to them.
"""

from __future__ import annotations

import json
import re

import pytest

import thestill.mcp.errors as errors_module
from tests.unit.web.test_mcp_http import USER_A, Harness, isolated_env  # noqa: F401
from thestill.mcp.errors import McpUserError, public_error_message
from thestill.mcp.identity import NotAuthenticatedError, ScopeError
from thestill.mcp.utils import NumericIdentifierRefused, parse_thestill_uri
from thestill.services.podcast_service import PodcastService
from thestill.web.middleware.rate_limit import RateLimitExceeded

# Shaped like the real thing: psycopg puts the offending value and the
# bound parameters in the message.
DB_ERROR = (
    "invalid input syntax for type uuid: \"better-offline\"\nCONTEXT:  unnamed portal parameter $2 = 's3cr3t-value'"
)
GENERIC = re.compile(r"^Internal error \(ref ([0-9a-f]{8})\)\. The details were logged on the server\.$")


class LogSpy:
    def __init__(self):
        self.records = []

    def error(self, event, **kwargs):
        self.records.append((event, kwargs))


@pytest.fixture
def log_spy(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(errors_module, "logger", spy)
    return spy


class TestPublicErrorMessage:
    def test_unexpected_exception_is_replaced_and_logged_in_full_under_the_same_ref(self, log_spy):
        exc = RuntimeError(DB_ERROR)
        message = public_error_message(exc, operation="find_mentions", episode_id="ep-1")

        match = GENERIC.match(message)
        assert match, message
        assert "uuid" not in message and "s3cr3t" not in message

        [(event, fields)] = log_spy.records
        assert event == "mcp_internal_error"
        assert fields["error_ref"] == match.group(1)
        assert fields["error"] == DB_ERROR and fields["error_type"] == "RuntimeError"
        assert fields["operation"] == "find_mentions" and fields["episode_id"] == "ep-1"
        assert fields["exc_info"] is exc

    @pytest.mark.parametrize(
        "exc",
        [
            McpUserError("Podcast not found: nope"),
            NumericIdentifierRefused("Numeric podcast index '1' is not accepted"),
            ScopeError("add_podcast", "follows"),
            NotAuthenticatedError("no connector identity"),
            RateLimitExceeded("quota exhausted"),
        ],
    )
    def test_authored_messages_pass_through_and_are_not_logged_as_internal_errors(self, log_spy, exc):
        assert public_error_message(exc, operation="x") == str(exc)
        assert log_spy.records == []

    def test_a_bare_valueerror_is_not_trusted_unless_the_call_site_vouches_for_it(self, log_spy):
        assert GENERIC.match(public_error_message(ValueError(DB_ERROR), operation="x"))
        vouched = public_error_message(ValueError("Podcast not found: 7"), operation="x", also_authored=(ValueError,))
        assert vouched == "Podcast not found: 7"

    def test_development_relays_the_detail_like_the_web_handler_and_still_logs(self, log_spy):
        message = public_error_message(RuntimeError("boom"), operation="x", expose_detail=True)
        assert message.startswith("RuntimeError: boom (ref ")
        assert len(log_spy.records) == 1

    def test_uri_errors_are_authored_and_still_valueerrors(self):
        with pytest.raises(ValueError) as excinfo:
            parse_thestill_uri("https://nope")
        assert isinstance(excinfo.value, McpUserError)


@pytest.fixture
def harness(isolated_env, tmp_path):  # noqa: F811
    return Harness(tmp_path)


class TestThroughTheHttpMount:
    """The real guard, session manager and handlers; only the failure is injected."""

    def test_tool_failure_shows_a_reference_not_the_database_error(self, harness, monkeypatch, log_spy):
        def explode(self, *args, **kwargs):
            raise RuntimeError(DB_ERROR)

        monkeypatch.setattr(PodcastService, "get_podcasts", explode)
        token = harness.mint(USER_A)
        with harness.client() as c:
            response = Harness.rpc(c, token, "tools/call", {"name": "list_podcasts", "arguments": {}})

        raw = response.text
        assert "uuid" not in raw and "s3cr3t" not in raw and "portal parameter" not in raw
        payload = json.loads(response.json()["result"]["content"][0]["text"])
        assert payload["success"] is False
        match = GENERIC.match(payload["error"])
        assert match, payload

        # The operator can get from what the user saw to the full error.
        [(_, fields)] = log_spy.records
        assert fields["error_ref"] == match.group(1)
        assert fields["error"] == DB_ERROR
        assert fields["operation"] == "list_podcasts" and fields["remote"] is True

    def test_resource_failure_shows_a_reference_not_the_database_error(self, harness, monkeypatch, log_spy):
        def explode(self, *args, **kwargs):
            raise RuntimeError(DB_ERROR)

        monkeypatch.setattr(PodcastService, "get_podcast", explode)
        token = harness.mint(USER_A)
        with harness.client() as c:
            response = Harness.rpc(c, token, "resources/read", {"uri": "thestill://podcasts/some-slug"})

        raw = response.text
        assert "uuid" not in raw and "s3cr3t" not in raw and "portal parameter" not in raw
        assert "Internal error (ref " in raw
        assert [fields["operation"] for _, fields in log_spy.records] == ["resources/read"]

    def test_authored_resource_failure_still_reaches_the_client(self, harness, log_spy):
        token = harness.mint(USER_A)
        with harness.client() as c:
            response = Harness.rpc(c, token, "resources/read", {"uri": "thestill://podcasts/no-such-podcast"})
        assert "Podcast not found: no-such-podcast" in response.text
        assert log_spy.records == []
