#!/usr/bin/env python3
# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #105 — what a crawler that does not run JavaScript reads on a page.

Fetches each URL with a crawler user agent and counts the words in the
``<body>`` once tags, ``<script>`` blocks (the data block and JSON-LD) and
``<head>`` are dropped. Today's head-only shell scores 0; a server-written
episode page should score 400 or more.

Usage:
    ./venv/bin/python scripts/body_word_count.py https://thestill.me/podcasts/<show>/episodes/<episode>
    ./venv/bin/python scripts/body_word_count.py --ua chrome --text <url>   # also print the text
    ./venv/bin/python scripts/body_word_count.py --min-words 400 <url> <url> ...   # exit 1 below the bar
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from html import unescape

USER_AGENTS = {
    "bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "chrome": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
}


def body_text(html: str) -> str:
    body = html.split("<body", 1)[-1]
    body = re.sub(r"<script\b.*?</script>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", unescape(body)).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--ua", choices=sorted(USER_AGENTS), default="bingbot")
    parser.add_argument("--min-words", type=int, default=0, help="exit 1 when any page has fewer words")
    parser.add_argument("--text", action="store_true", help="print the body text too")
    args = parser.parse_args()
    short = []
    for url in args.urls:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENTS[args.ua]})
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - operator-supplied URL
            raw = response.read()
            text = body_text(raw.decode("utf-8", "replace"))
        words = len(text.split())
        sys.stdout.write(f"{words:6d} words  {len(raw):7d} bytes  {url}\n")
        if args.text:
            sys.stdout.write(f"{text}\n\n")
        if words < args.min_words:
            short.append(url)
    return 1 if short else 0


if __name__ == "__main__":
    sys.exit(main())
