#!/usr/bin/env python3
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
"""Call every tool of a live Thestill connector and report what worked.

The check a person would do by hand before a directory submission or after
a deploy: connect as a real MCP client, confirm every tool carries a title
and behaviour hints, then call each tool with valid arguments taken from
the previous answers.

Usage::

    export THESTILL_MCP_URL='https://thestill.me/mcp/<token>'   # Settings → Advanced
    ./venv/bin/python scripts/check_connector.py                 # read tools only
    ./venv/bin/python scripts/check_connector.py --write \\
        --follow-url https://example.com/feed.xml \\
        --import-url https://podcasts.apple.com/…?i=…

The address is the *personal connector URL*, which is a credential: it is
read from the environment, never from the command line, and never printed.

Read tools change nothing. ``--write`` also runs the write tools, on the
account the URL belongs to, so use a test account:

- ``send_to_inbox`` puts the searched episode in the inbox (it stays there).
- ``--follow-url`` follows that show and unfollows it again.
- ``--import-url`` imports that episode, which runs the pipeline.

Operator (pipeline) tools are never called. A tool the server lists that
this script neither calls nor deliberately skips is reported as a failure,
so a new tool cannot go unchecked. Exits 1 on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import Client  # noqa: E402

from thestill.mcp.registration import tool_result_ok  # noqa: E402

URL_ENV = "THESTILL_MCP_URL"
REDACTED = "<connector-url>"


def redactor(url: Optional[str]) -> Callable[[str], str]:
    """Strip the personal connector URL, and its token, from text.

    The token is the URL's last path segment; it is matched on its own so a
    redirect to another scheme, host or path cannot carry it through.
    """
    secrets = []
    if url:
        secrets.append(url.rstrip("/"))
        token = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        if len(token) >= 8:
            secrets.append(token)

    def redact(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, REDACTED)
        return text

    return redact


class _RedactingFilter(logging.Filter):
    """Rewrites every log record on its way out so the URL never reaches stderr."""

    def __init__(self, redact: Callable[[str], str]) -> None:
        super().__init__()
        self._redact = redact

    def filter(self, record: logging.LogRecord) -> bool:
        message = self._redact(record.getMessage())
        if record.exc_info:
            message += " (" + self._redact(f"{record.exc_info[0].__name__}: {record.exc_info[1]}") + ")"
        record.msg, record.args, record.exc_info, record.exc_text = message, None, None, None
        return True


#: Tools that are never called: they queue or run pipeline work for everyone.
OPERATOR_TOOLS = frozenset(
    {
        "refresh_feeds",
        "download_episodes",
        "downsample_audio",
        "transcribe_episodes",
        "clean_transcripts",
        "process_episode",
        "summarize_episodes",
    }
)
WRITE_TOOLS = frozenset({"add_podcast", "remove_podcast", "import_episode", "send_to_inbox"})


@dataclass
class Options:
    query: str = "AI agents"
    entity: str = "Nvidia"
    write: bool = False
    follow_url: Optional[str] = None
    import_url: Optional[str] = None


@dataclass
class Outcome:
    tool: str
    status: str  # ok | FAIL | skipped
    detail: str = ""
    ms: Optional[int] = None


@dataclass
class Report:
    outcomes: List[Outcome] = field(default_factory=list)
    redact: Callable[[str], str] = field(default=lambda text: text, repr=False)

    def add(self, tool: str, status: str, detail: str = "", ms: Optional[int] = None) -> None:
        self.outcomes.append(Outcome(tool, status, self.redact(detail), ms))

    @property
    def failed(self) -> List[Outcome]:
        return [o for o in self.outcomes if o.status == "FAIL"]

    def render(self) -> str:
        width = max((len(o.tool) for o in self.outcomes), default=4)
        lines = []
        for o in self.outcomes:
            took = f"{o.ms:>6} ms" if o.ms is not None else " " * 9
            lines.append(f"{o.status:<8} {o.tool:<{width}} {took}  {o.detail}".rstrip())
        ran = sum(1 for o in self.outcomes if o.status == "ok")
        skipped = sum(1 for o in self.outcomes if o.status == "skipped")
        lines.append("")
        lines.append(f"{ran} passed, {len(self.failed)} failed, {skipped} skipped")
        return "\n".join(lines)


def _payload(result: Any) -> Dict[str, Any]:
    """The tool's JSON answer, or {} when it answered with plain text."""
    first = result.content[0] if result.content else None
    text = getattr(first, "text", "") or ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error_text(result: Any) -> str:
    payload = _payload(result)
    message = payload.get("error") or payload.get("message")
    if message:
        return str(message)
    first = result.content[0] if result.content else None
    return getattr(first, "text", "") or "no content"


def check_annotations(tools: List[Any], report: Report) -> None:
    """Every listed tool has a title and both hints set (spec #109)."""
    missing = []
    for tool in tools:
        hints = tool.annotations
        if not tool.title or hints is None or hints.read_only_hint is None or hints.destructive_hint is None:
            missing.append(tool.name)
    if missing:
        report.add("tools/list", "FAIL", "no title or hints: " + ", ".join(sorted(missing)))
    else:
        report.add("tools/list", "ok", f"{len(tools)} tools, each with a title and hints")


async def run_checks(client: Any, options: Options, url: Optional[str] = None) -> Report:
    """Call each tool the connector lists; later calls use earlier answers.

    ``url`` is the connector address, redacted from every reported detail.
    """
    report = Report(redact=redactor(url))
    tools = (await client.list_tools()).tools
    listed = {tool.name for tool in tools}
    check_annotations(tools, report)
    handled: set[str] = set()

    async def call(
        name: str,
        arguments: Dict[str, Any],
        describe: Callable[[Dict[str, Any]], str] = lambda _p: "",
    ) -> Optional[Dict[str, Any]]:
        """Call one tool; None when it is not listed or failed."""
        handled.add(name)
        if name not in listed:
            return None
        started = time.monotonic()
        try:
            result = await client.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - one broken tool must not hide the rest
            report.add(
                name,
                "FAIL",
                report.redact(f"{type(exc).__name__}: {exc}")[:160],
                int((time.monotonic() - started) * 1000),
            )
            return None
        took = int((time.monotonic() - started) * 1000)
        if not tool_result_ok(result):
            report.add(name, "FAIL", report.redact(_error_text(result))[:160], took)
            return None
        payload = _payload(result)
        report.add(name, "ok", describe(payload), took)
        return payload

    def skip(name: str, why: str) -> None:
        handled.add(name)
        if name in listed:
            report.add(name, "skipped", why)

    def count(key: str) -> Callable[[Dict[str, Any]], str]:
        return lambda p: f"{len(p.get(key) or [])} {key}"

    await call("get_status", {})
    await call("list_podcasts", {}, count("podcasts"))
    await call("list_inbox", {"limit": 5})
    await call("get_analytics", {"range": "7d"})

    search = await call("search_corpus", {"query": options.query, "limit": 5}, count("results"))
    hit = (search or {}).get("results") or []
    first = hit[0] if hit else None
    needs_hit = ("list_episodes", "get_summary", "get_transcript", "get_episode_clip", "list_quotes_by")
    if first is None:
        for name in needs_hit:
            handled.add(name)
            if name in listed:
                report.add(name, "FAIL", f"search for {options.query!r} found nothing to call it with")
    else:
        podcast_id, episode_id = first.get("podcast_id"), first.get("episode_id")
        await call("list_episodes", {"podcast_id": podcast_id, "limit": 5}, count("episodes"))
        await call("get_summary", {"podcast_id": podcast_id, "episode_id": episode_id})
        await call("get_transcript", {"podcast_id": podcast_id, "episode_id": episode_id})
        await call("get_episode_clip", {"episode_id": episode_id, "start_ms": first.get("start_ms") or 0})
        speaker = first.get("speaker")
        if speaker:
            await call("list_quotes_by", {"speaker": speaker, "limit": 5}, lambda _p: f"speaker {speaker}")
        else:
            skip("list_quotes_by", "the search hit names no speaker")

    await call("get_entity", {"id_or_name": options.entity})
    await call("find_mentions", {"entity": options.entity, "limit": 5})
    await call("list_episodes_by_entity", {"has_entity": [options.entity], "limit": 5})

    if not options.write:
        for name in sorted(WRITE_TOOLS):
            skip(name, "write tool; run with --write")
    else:
        if first is not None:
            await call("send_to_inbox", {"episode_id": first.get("episode_id")})
        else:
            skip("send_to_inbox", "no search hit to send")
        if options.follow_url:
            added = await call("add_podcast", {"url": options.follow_url})
            followed = (added or {}).get("podcast") or {}
            target = followed.get("id") or followed.get("slug") or (added or {}).get("podcast_id")
            if target:
                await call("remove_podcast", {"podcast_id": target})
            else:
                handled.add("remove_podcast")
                if "remove_podcast" in listed:
                    report.add("remove_podcast", "FAIL", "add_podcast returned no podcast id to unfollow")
        else:
            skip("add_podcast", "pass --follow-url")
            skip("remove_podcast", "pass --follow-url")
        if options.import_url:
            await call("import_episode", {"url": options.import_url})
        else:
            skip("import_episode", "pass --import-url")

    for name in sorted(OPERATOR_TOOLS):
        skip(name, "operator tool; never called")

    for name in sorted(listed - handled):
        report.add(name, "FAIL", "listed by the server but not covered by this check")
    return report


def _parse(argv: Optional[List[str]]) -> Options:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--query", default=Options.query, help="search text the read tools start from")
    parser.add_argument("--entity", default=Options.entity, help="person, company or topic for the entity tools")
    parser.add_argument("--write", action="store_true", help="also run the write tools (use a test account)")
    parser.add_argument("--follow-url", help="with --write: a show to follow and unfollow again")
    parser.add_argument("--import-url", help="with --write: an episode link to import")
    args = parser.parse_args(argv)
    if (args.follow_url or args.import_url) and not args.write:
        parser.error("--follow-url and --import-url need --write")
    return Options(
        query=args.query,
        entity=args.entity,
        write=args.write,
        follow_url=args.follow_url,
        import_url=args.import_url,
    )


async def _main(options: Options, url: str) -> Report:
    async with Client(url) as client:
        return await run_checks(client, options, url=url)


def main(argv: Optional[List[str]] = None) -> int:
    options = _parse(argv)
    url = os.environ.get(URL_ENV, "").strip()
    if not url:
        sys.stderr.write(f"Set {URL_ENV} to your personal connector URL (thestill Settings → Advanced).\n")
        return 2
    # The MCP transport logs redirects with their target, which can be the
    # connector URL: every record goes through one redacting stderr handler.
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_RedactingFilter(redactor(url)))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    try:
        report = asyncio.run(_main(options, url))
    except Exception as exc:  # noqa: BLE001 - the URL must never reach the terminal through a traceback
        sys.stderr.write(f"Could not reach the connector: {type(exc).__name__}\n")
        return 1
    sys.stdout.write(report.render() + "\n")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
