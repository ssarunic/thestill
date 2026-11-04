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

"""Decode HTTP bodies without trusting ``requests.Response.text``.

``Response.text`` picks its codec from the ``Content-Type`` header alone,
and falls back to ISO-8859-1 for any ``text/*`` response that names no
charset. Apple's podcast pages are served as bare ``text/html`` with a
UTF-8 body, so every ``—`` became ``â\x80\x94`` in imported titles
("The Risks and Opportunities of an AI Future â with Eric Schmidt",
2026-11-04). Every HTTP body Thestill turns into stored text goes through
``response_text`` (or ``decode_feed_body`` for raw bytes).
"""

from __future__ import annotations

import codecs
import re
from typing import Any, List, Optional, Tuple

# An XML declaration with an explicit encoding, e.g. <?xml version="1.0" encoding="ISO-8859-1"?>
_XML_DECL_ENCODING_RE = re.compile(rb"^\s*<\?xml[^>]*?encoding=[\"']([A-Za-z0-9._-]+)[\"']")
# A charset parameter on a Content-Type header value.
_HEADER_CHARSET_RE = re.compile(r"charset=[\"']?([A-Za-z0-9._-]+)", re.IGNORECASE)


def decode_feed_body(raw: bytes, content_type: Optional[str] = None) -> Tuple[str, str]:
    """Decode an RSS/Atom HTTP body to text without trusting ``Response.text``.

    ``requests.Response.text`` picks its codec from the ``Content-Type``
    header alone: ISO-8859-1 for ``text/*`` without a charset (RFC 2616),
    and a statistical guess (charset_normalizer) for ``application/xml``
    and friends without one. Feeds routinely omit the charset, and the
    guess depends on the body of the day — a mostly-ASCII feed with a few
    accented names comes back as cp1252 on one host and UTF-8 on another.
    A wrong guess turns every ``å`` into ``Ã¥`` and, because feedparser is
    then handed a *str*, the XML declaration that says UTF-8 is never
    consulted. That mojibake was stored in episode descriptions, copied
    into facts, speaker names and cleaned transcripts, and indexed for
    search ("Max JungestÃ¥l", 2026-09-29).

    Order of trust: a BOM; strict UTF-8 (a body that decodes as UTF-8 with
    non-ASCII content is UTF-8 — a Latin-1 body virtually never passes);
    the XML declaration; the header charset; finally cp1252 with
    replacement so a broken feed still parses instead of raising.

    Returns ``(text, encoding_used)`` so the caller can log the codec.
    """
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace"), "utf-8-sig"
    for bom, enc in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(bom):
            return raw[len(bom) :].decode(enc, errors="replace"), enc
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    candidates: List[str] = []
    decl = _XML_DECL_ENCODING_RE.match(raw)
    if decl:
        candidates.append(decl.group(1).decode("ascii", errors="ignore"))
    if content_type:
        header = _HEADER_CHARSET_RE.search(content_type)
        if header:
            candidates.append(header.group(1))
    for enc in candidates:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("cp1252", errors="replace"), "cp1252-replace"


def response_text(resp: Any) -> str:
    """``resp``'s body as text, decoded by ``decode_feed_body`` rather than ``resp.text``."""
    text, _ = decode_feed_body(resp.content, resp.headers.get("Content-Type"))
    return text
