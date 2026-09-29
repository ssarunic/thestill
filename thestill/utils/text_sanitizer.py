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

"""Sanitize LLM-produced text before it is persisted.

LLMs occasionally emit raw control characters — the incident that motivated
this module was Gemini's clean stage returning ``saut\\u0000 onions`` for
"sauté onions" (U+0000 where the ``é`` belongs). SQLite stores an embedded NUL
silently, so the corruption propagated invisibly into ``chunks.text`` and
``entity_mentions.quote_excerpt`` until the Postgres migration (which forbids
NUL in ``text``) surfaced it.

``sanitize_text`` strips the C0/C1 control ranges EXCEPT the whitespace we
legitimately persist: tab (U+0009), newline (U+000A), and carriage return
(U+000D). Everything printable — accents, CJK, emoji, quotes, markdown
syntax — passes through untouched. Callers that need observability use the
returned count to log how many characters were removed (never strip silently:
spec #42 FM-4).
"""

from __future__ import annotations

import re

import ftfy

# C0 controls minus \t \n \r, plus DEL and the C1 range. As *codepoints* —
# this is unicode-aware (U+0085 NEL etc.), not byte munging.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# One UTF-8 sequence as it looks after a cp1252 (or Latin-1) mis-decode: the
# lead byte (U+00C2–U+00F4 as a character) followed by exactly as many
# continuation bytes as that lead demands, each shown as U+0080–U+00BF or
# as the cp1252 glyph for 0x80–0x9F (€ ‚ ƒ „ … † ‡ ˆ ‰ Š ‹ Œ Ž ‘ ’ “ ” • – — ˜ ™ š › œ ž Ÿ).
_CONT = "[\u0080-\u00bf\u20ac\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039\u0152\u017d\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122\u0161\u203a\u0153\u017e\u0178]"
_MOJIBAKE_RUN_RE = re.compile(
    "(?:"
    + "|".join(
        (
            f"[\u00c2-\u00df]{_CONT}",
            f"[\u00e0-\u00ef]{_CONT}{{2}}",
            f"[\u00f0-\u00f4]{_CONT}{{3}}",
        )
    )
    + ")+"
)


def sanitize_text(text: str) -> tuple[str, int]:
    """Strip disallowed control characters from ``text``.

    Returns ``(clean_text, removed_count)``. ``removed_count`` is 0 for the
    overwhelmingly common clean case, so callers can gate logging on it.
    """
    clean, count = _CONTROL_CHARS_RE.subn("", text)
    return clean, count


def repair_mojibake(text: str) -> tuple[str, bool]:
    """Undo double-encoded UTF-8: ``"Max JungestÃ¥l"`` → ``"Max Jungestål"``.

    The pattern is UTF-8 bytes that were decoded once as Latin-1 / cp1252
    (or a sibling single-byte codec) and re-encoded as UTF-8. It entered
    the corpus through ``requests.Response.text`` guessing the charset of
    a feed body (fixed at the source in ``media_source.decode_feed_body``)
    and then spread from episode descriptions into facts, speaker names,
    cleaned transcripts, summaries and the search index.

    Delegates to ``ftfy.fix_encoding`` — encoding repair only, no quote or
    entity normalisation. Applied line by line: one unrecoverable fragment
    (a lead byte whose continuation bytes were stripped as C1 controls by
    ``sanitize_text``) makes ftfy leave its whole input alone, and that
    must not veto the rest of a transcript. Text that is already correct
    passes through unchanged; pure-ASCII input is the fast path.

    Returns ``(repaired_text, changed)``.
    """
    if not text or text.isascii():
        return text, False
    lines = text.split("\n")
    fixed = [line if line.isascii() else _repair_line(line) for line in lines]
    changed = fixed != lines
    return ("\n".join(fixed) if changed else text), changed


def _repair_line(line: str) -> str:
    """ftfy first, then each remaining mojibake run on its own.

    ftfy leaves a line alone as soon as it also holds *correct* non-ASCII
    text — and every stored chunk is ``"Speaker: text"`` where the cleaned
    text carries genuine curly quotes and dashes while the speaker name
    carries the damage ("Max JungestÃ¥l: So we’re doubling"). The run pass
    re-encodes only the matched run (cp1252, then Latin-1) and keeps the
    result only when it decodes as UTF-8, so a real "é…" or "Ã" on its own
    never matches: the pattern demands exactly the continuation count the
    lead byte implies.
    """
    # ftfy may also fix only part of a line ("MÃ¼ller & JungestÃ¥l" → "Müller
    # & JungestÃ¥l"), so the run pass always follows it.
    return _MOJIBAKE_RUN_RE.sub(_decode_run, ftfy.fix_encoding(line))


def _decode_run(match: "re.Match[str]") -> str:
    run = match.group(0)
    for codec in ("cp1252", "latin-1"):
        try:
            return run.encode(codec).decode("utf-8")
        except UnicodeError:
            continue
    return run
