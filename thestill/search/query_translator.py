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

"""Spec #28 §O2 — translate user query strings to FTS5-safe MATCH.

The corpus search advertises operator syntax to MCP/REST callers
(``"sovereign AI" AND -nvidia``, ``speaker:friedberg``). Those tokens
can't be passed straight into SQLite FTS5: ``-foo`` is not a valid
FTS5 operator, and ``speaker:`` is column syntax against a column
that doesn't exist on ``chunks_fts`` (the FTS table only indexes
``text``). Both raise ``OperationalError`` and 500 the request.

This module parses the user input and returns a structured form the
backend can route safely:

- ``fts_match``      — an FTS5-safe MATCH expression for the ``text``
                        column.
- ``embedding_text`` — operator-stripped plaintext for the semantic
                        leg's encoder. The encoder shouldn't see
                        ``speaker:`` or ``-`` artefacts.
- ``speaker``        — case-insensitive substring filter applied to
                        ``chunks.speaker`` (joined separately).

Supported syntax (the rest is conservatively passed through as terms):

- ``"phrase one"``       — preserved as an FTS5 phrase.
- ``-term``              — exclusion (translated to ``NOT term``).
- ``AND`` / ``OR``       — uppercase, kept as FTS5 operators.
- ``speaker:VALUE``      — extracted as a side filter, not FTS.
- ``column:VALUE``       — any other unknown column is treated as a
                            literal phrase (quoted) for FTS5 so the
                            colon doesn't trigger column-restricted
                            syntax against the single-column schema.
                            The embedding model still sees the raw
                            string — chunk text is stored as
                            ``"<speaker>: <text>"`` so embeddings of
                            colon-prefixed queries are meaningful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class TranslatedQuery:
    """A user query split into FTS5-safe parts plus a speaker filter."""

    fts_match: str
    embedding_text: str
    speaker: Optional[str] = None


# Characters FTS5 treats as punctuation/operators inside a bareword.
# Anything containing one of these gets quoted to keep it as a literal
# phrase.
_FTS5_SPECIAL_RE = re.compile(r"[^A-Za-z0-9_*]")

# Operator tokens that pass straight through to the FTS5 expression.
_FTS5_OPERATORS = {"AND", "OR", "NOT"}


def translate_lexical_query(raw: str) -> TranslatedQuery:
    """Translate a user query to an FTS5-safe MATCH expression.

    Returns a sentinel ``TranslatedQuery`` for empty input — callers
    decide whether to skip the FTS path or treat as an error.
    """
    text = (raw or "").strip()
    if not text:
        return TranslatedQuery(fts_match="", embedding_text="", speaker=None)

    tokens = _tokenize(text)

    positives: List[str] = []
    negatives: List[str] = []
    operators_in_order: List[Tuple[int, str]] = []  # (insert_pos, operator)
    embedding_parts: List[str] = []
    speaker_filter: Optional[str] = None

    for tok in tokens:
        if not tok:
            continue
        kind, value, embed_value = _classify(tok)
        if kind == "speaker":
            # Last speaker wins — multiple speaker: filters are an
            # invalid combination we don't try to AND together.
            speaker_filter = value
            continue
        if kind == "operator":
            # Track operator placement so we can reinsert between the
            # surrounding positives. Negatives are collected separately
            # and joined with NOT (...) at the tail.
            operators_in_order.append((len(positives), value))
            continue
        if kind == "negative":
            negatives.append(value)
            embedding_parts.append(embed_value)
            continue
        # positive (phrase or bareword, possibly an unknown column-prefixed
        # token that was quoted to keep FTS5 happy).
        positives.append(value)
        embedding_parts.append(embed_value)

    fts_match = _assemble(positives, operators_in_order, negatives)
    embedding_text = " ".join(embedding_parts).strip()
    if not embedding_text:
        # Speaker-only or operator-only input — fall back to the raw
        # text so the embedding model still has something to encode.
        embedding_text = text
    return TranslatedQuery(
        fts_match=fts_match,
        embedding_text=embedding_text,
        speaker=speaker_filter,
    )


def _tokenize(text: str) -> List[str]:
    """Split on whitespace, preserving quoted phrases as one token."""
    out: List[str] = []
    buf: List[str] = []
    in_quote = False
    for ch in text:
        if ch == '"':
            buf.append(ch)
            in_quote = not in_quote
            continue
        if ch.isspace() and not in_quote:
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _classify(tok: str) -> Tuple[str, str, str]:
    """Classify a single token into (kind, fts_value, embedding_value).

    kind: ``positive`` | ``negative`` | ``speaker`` | ``operator``

    ``fts_value`` is the FTS5-safe representation; ``embedding_value``
    is the plaintext form fed to the encoder. Speaker tokens return
    only the speaker substring (in ``fts_value``) and an empty
    embedding value — speaker filters shouldn't pollute the encoder.
    """
    if tok in _FTS5_OPERATORS:
        return "operator", tok, ""
    # Speaker filter (case-insensitive prefix): ``speaker:foo``,
    # ``Speaker:"two words"``.
    lower = tok.lower()
    if lower.startswith("speaker:") and len(tok) > len("speaker:"):
        value = _strip_quotes(tok.split(":", 1)[1])
        return "speaker", value, ""
    if tok.startswith("-") and len(tok) > 1:
        bare = tok[1:]
        return "negative", _quote_if_needed(bare), _strip_quotes(bare)
    return "positive", _quote_if_needed(tok), _strip_quotes(tok)


def _assemble(
    positives: List[str],
    operators: List[Tuple[int, str]],
    negatives: List[str],
) -> str:
    """Build the final FTS5 MATCH expression from the parsed parts."""
    if not positives and not negatives:
        return ""

    if positives:
        # Reinsert explicit AND/OR operators at the positions they
        # appeared, defaulting to space (implicit AND) elsewhere.
        op_at = {pos: op for pos, op in operators}
        parts: List[str] = [positives[0]]
        for i in range(1, len(positives)):
            sep = op_at.get(i, "AND")
            parts.append(sep)
            parts.append(positives[i])
        positive_expr = " ".join(parts)
    else:
        # Negatives without positives — synthesise a degenerate
        # positive so FTS5 has something to scan against. Use the
        # first negative as a positive (better than 500-ing).
        positive_expr = negatives[0]
        negatives = negatives[1:]

    if not negatives:
        return positive_expr

    if len(negatives) == 1:
        neg_expr = negatives[0]
    else:
        # FTS5 NOT is binary; multiple negatives go inside ``NOT (a OR b)``.
        neg_expr = "(" + " OR ".join(negatives) + ")"
    return f"{positive_expr} NOT {neg_expr}"


def _quote_if_needed(token: str) -> str:
    """Wrap a bareword in double quotes if it'd parse as an operator
    or contains FTS5-special characters.

    Already-quoted phrases are returned unchanged.
    """
    if not token:
        return token
    if token.startswith('"') and token.endswith('"'):
        return token
    # Tokens that look like FTS5 operators must be quoted to be
    # treated as terms.
    if token.upper() in _FTS5_OPERATORS:
        return f'"{token}"'
    if _FTS5_SPECIAL_RE.search(token):
        # Escape any embedded double-quotes per FTS5 quoting rules.
        escaped = token.replace('"', '""')
        return f'"{escaped}"'
    return token


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value
