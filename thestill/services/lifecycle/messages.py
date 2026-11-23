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

"""The lifecycle sequence (spec #118): which messages exist, when each may
go out, when it stops making sense, whom it applies to, and what it says.

A message is a condition plus an earliest time, never "send at T": the
planner checks ``applies`` on the facts it reads at plan time, and the
sender checks it again just before sending, so a reader who followed a
show at hour 23 never gets "follow a show" at hour 24.

Copy is markdown in the subset ``email_markup.markdown_to_html`` renders.
Links are app-relative; the renderer makes them absolute. Every link here
must point at a page that exists (``thestill/web/frontend/src/App.tsx``).
Changing a message's ``key`` makes it a new message: users who got the old
one get the new one too.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Dict, List, Tuple

from ...models.user_message import LifecycleFacts


@dataclass(frozen=True)
class MessageContent:
    subject: str
    #: Markdown body, without greeting or footer (the renderer adds both).
    body: str


@dataclass(frozen=True)
class LifecycleMessage:
    key: str
    #: Earliest send, measured from signup.
    send_after: timedelta
    #: From signup; past this the message is never queued (a day-1 welcome
    #: is not sent on day 9 after an outage).
    expires_after: timedelta
    applies: Callable[[LifecycleFacts], bool]
    compose: Callable[[LifecycleFacts], MessageContent]


def _briefing_line(facts: LifecycleFacts) -> str:
    when = ""
    if facts.briefing_enabled and facts.briefing_hour_local is not None:
        when = f" at {facts.briefing_hour_local:02d}:00 your time"
    line = f"- **A briefing{when}.** [Briefings](/briefings) gather everything new from your shows into one read."
    if not facts.briefing_email_enabled:
        line += " Want it by email? Turn on briefing emails in [Settings](/settings)."
    return line


def _welcome(facts: LifecycleFacts) -> MessageContent:
    if facts.follow_count == 0:
        body = "\n".join(
            [
                "Thanks for signing up. thestill turns the podcasts you follow into summaries and "
                "transcripts you can read, search and share.",
                "",
                "The first step is to follow a show:",
                "",
                "- Browse [Top podcasts](/top) and press **Follow** on one you like.",
                "- Or open [Podcasts](/podcasts) and paste an RSS, Apple Podcasts or Spotify show link.",
                "",
                "From then on, new episodes arrive in your [Inbox](/inbox) with a summary and a full " "transcript.",
            ]
        )
    else:
        shows = "a show" if facts.follow_count == 1 else f"{facts.follow_count} shows"
        body = "\n".join(
            [
                f"Thanks for signing up. You follow {shows}, so here is what happens next:",
                "",
                "- **New episodes land in your [Inbox](/inbox)** with a summary and a full transcript.",
                _briefing_line(facts),
                "- **One episode from somewhere else?** Use **Import** in your [Inbox](/inbox) and paste "
                "a YouTube, Apple Podcasts or Spotify episode link.",
            ]
        )
    return MessageContent(subject="Welcome to thestill", body=body)


def _follow_nudge(facts: LifecycleFacts) -> MessageContent:
    body = "\n".join(
        [
            "Your inbox is still empty, because thestill only reads the shows you follow.",
            "",
            "[Top podcasts](/top) lists the most popular shows in your country. Press **Follow** on one, "
            "and its latest episodes arrive in your [Inbox](/inbox) with summaries you can read in a few "
            "minutes.",
            "",
            "Don't see your show? Paste its RSS, Apple Podcasts or Spotify link in [Podcasts](/podcasts).",
        ]
    )
    return MessageContent(subject="Pick a show to fill your inbox", body=body)


def _claude_connector(facts: LifecycleFacts) -> MessageContent:
    body = "\n".join(
        [
            "Did you know you can connect Claude to thestill?",
            "",
            "Once connected, you can ask Claude about the episodes in your inbox: \"what did this week's "
            'episodes say about interest rates?", "summarise the guest\'s argument", or "find where they '
            'talked about AI". Claude answers from the transcripts and links back to the moment in the episode.',
            "",
            "It takes a minute: [see how to connect Claude](/claude).",
        ]
    )
    return MessageContent(subject="Ask Claude about your podcasts", body=body)


def _search_and_people(facts: LifecycleFacts) -> MessageContent:
    body = "\n".join(
        [
            "Two things that are easy to miss:",
            "",
            "- **Search every transcript.** [Search](/search) finds the moment someone said something, "
            "across all the shows thestill has transcribed, not just yours.",
            "- **Track a guest across shows.** Names in a summary open a card for that person "
            "or company, with a link to every episode they appear in.",
        ]
    )
    return MessageContent(subject="Search what was said, not just titles", body=body)


#: The sequence, in send order. Keys are permanent (see the module note).
SEQUENCE: Tuple[LifecycleMessage, ...] = (
    LifecycleMessage(
        key="welcome",
        send_after=timedelta(hours=24),
        expires_after=timedelta(days=3),
        applies=lambda facts: True,
        compose=_welcome,
    ),
    LifecycleMessage(
        key="follow_nudge",
        send_after=timedelta(days=3),
        expires_after=timedelta(days=7),
        applies=lambda facts: facts.follow_count == 0,
        compose=_follow_nudge,
    ),
    LifecycleMessage(
        key="claude_connector",
        send_after=timedelta(days=7),
        expires_after=timedelta(days=12),
        applies=lambda facts: not facts.connector_connected,
        compose=_claude_connector,
    ),
    LifecycleMessage(
        key="search_and_people",
        send_after=timedelta(days=14),
        expires_after=timedelta(days=21),
        # Only for readers who got going: search tips don't help an empty inbox.
        applies=lambda facts: facts.follow_count > 0,
        compose=_search_and_people,
    ),
)


def by_key(sequence: Tuple[LifecycleMessage, ...] = SEQUENCE) -> Dict[str, LifecycleMessage]:
    return {message.key: message for message in sequence}


def keys(sequence: Tuple[LifecycleMessage, ...] = SEQUENCE) -> List[str]:
    return [message.key for message in sequence]
