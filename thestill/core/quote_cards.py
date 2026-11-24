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

"""Quote card images (spec #114 §3): the picture a short link previews as.

Pure rendering: text and optional artwork bytes in, PNG bytes out. No
network, no storage. The quote is transcript text and the names are entity
data; both are drawn as glyphs, never interpreted, with control characters
removed (FM-7).

Fonts are Newsreader (the quote) and Inter (everything else), bundled under
``thestill/assets/fonts`` with their OFL licences.
"""

import io
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

#: Bumped when the card's look changes, so cached files are not reused.
TEMPLATE_VERSION = 1

OG_SIZE = (1200, 630)

_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_SERIF = "Newsreader.ttf"
_SANS = "Inter.ttf"

_BACKGROUND = (24, 35, 56)
_TEXT = (248, 246, 240)
_MUTED = (146, 160, 184)
_ACCENT = (222, 170, 110)
_RULE = (44, 60, 90)
_TILE = (58, 76, 110)

_MARGIN = 64
_ARTWORK = 264
_QUOTE_MAX_PX = 56
_QUOTE_MIN_PX = 28
_QUOTE_MAX_LINES = 8
_LINE_SPACING = 1.22


@dataclass(frozen=True)
class QuoteCard:
    """What one card says."""

    quote: str
    #: The speaker's name, or None for "a guest on <show>".
    speaker: Optional[str]
    show: str
    episode_title: str
    #: The short link as text, without a scheme: ``thestill.me/q/abc``.
    short_url: str
    #: Episode or show artwork as downloaded, or None for a neutral tile.
    artwork: Optional[bytes] = None


@lru_cache(maxsize=64)
def _font(name: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(_FONT_DIR / name), size)
    # Variable fonts: set each axis by name (the two files order them differently).
    axes = []
    for axis in font.get_variation_axes():
        label = axis["name"].decode() if isinstance(axis["name"], bytes) else str(axis["name"])
        if "eight" in label:
            axes.append(min(axis["maximum"], max(axis["minimum"], weight)))
        else:  # optical size
            axes.append(min(axis["maximum"], max(axis["minimum"], size)))
    if axes:
        font.set_variation_by_axes(axes)
    return font


def clean_text(text: str) -> str:
    """One line of printable text: control characters out, whitespace collapsed."""
    kept = "".join(" " if ch.isspace() else ch for ch in text if ch.isspace() or unicodedata.category(ch)[0] != "C")
    return " ".join(kept.split())


def _split_token(word: str, font: ImageFont.FreeTypeFont, width: float) -> List[str]:
    """A word wider than the column (a URL, say), cut into pieces that fit."""
    pieces: List[str] = []
    piece = ""
    for ch in word:
        if piece and font.getlength(piece + ch) > width:
            pieces.append(piece)
            piece = ch
        else:
            piece += ch
    if piece:
        pieces.append(piece)
    return pieces


def _greedy(words: List[str], font: ImageFont.FreeTypeFont, width: float) -> List[str]:
    lines: List[str] = []
    line = ""
    for word in words:
        candidate = f"{line} {word}" if line else word
        if line and font.getlength(candidate) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def wrap_balanced(text: str, font: ImageFont.FreeTypeFont, width: int) -> List[str]:
    """Wrap to ``width``, then narrow the lines as far as the same line
    count allows, so the last line is not a stray word. No line is wider
    than ``width``: a single word that is gets cut."""
    words: List[str] = []
    for word in text.split():
        words.extend(_split_token(word, font, width) if font.getlength(word) > width else [word])
    lines = _greedy(words, font, width)
    low, high = 0.0, float(width)
    for _ in range(12):
        middle = (low + high) / 2
        attempt = _greedy(words, font, middle)
        if len(attempt) <= len(lines) and all(font.getlength(line) <= width for line in attempt):
            high, lines = middle, attempt
        else:
            low = middle
    return lines


def _fit_quote(text: str, width: int, height: int) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    """The largest quote size whose balanced lines fit the box."""
    for size in range(_QUOTE_MAX_PX, _QUOTE_MIN_PX - 1, -2):
        font = _font(_SERIF, size, 420)
        lines = wrap_balanced(text, font, width)
        if len(lines) <= _QUOTE_MAX_LINES and len(lines) * size * _LINE_SPACING <= height:
            return font, lines
    font = _font(_SERIF, _QUOTE_MIN_PX, 420)
    lines = wrap_balanced(text, font, width)[:_QUOTE_MAX_LINES]
    if lines:
        lines[-1] = _truncate(lines[-1] + " …", font, width)
    return font, lines


def _truncate(text: str, font: ImageFont.FreeTypeFont, width: float) -> str:
    """``text`` cut to ``width`` with an ellipsis."""
    if font.getlength(text) <= width:
        return text
    while text and font.getlength(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _artwork_tile(artwork: Optional[bytes], show: str) -> Image.Image:
    tile: Optional[Image.Image] = None
    if artwork:
        try:
            with Image.open(io.BytesIO(artwork)) as source:
                source.draft("RGB", (_ARTWORK * 2, _ARTWORK * 2))
                image = source.convert("RGB")
            side = min(image.size)
            left, top = (image.width - side) // 2, (image.height - side) // 2
            tile = image.crop((left, top, left + side, top + side)).resize((_ARTWORK, _ARTWORK), Image.LANCZOS)
        except Exception:  # an image we cannot read is the same as none
            tile = None
    if tile is None:
        tile = Image.new("RGB", (_ARTWORK, _ARTWORK), _TILE)
        initial = (show.strip()[:1] or "•").upper()
        font = _font(_SERIF, 140, 500)
        draw = ImageDraw.Draw(tile)
        draw.text((_ARTWORK / 2, _ARTWORK / 2), initial, font=font, fill=_TEXT, anchor="mm")
    mask = Image.new("L", (_ARTWORK, _ARTWORK), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, _ARTWORK - 1, _ARTWORK - 1), radius=28, fill=255)
    tile.putalpha(mask)
    return tile


def render_og(card: QuoteCard) -> bytes:
    """The 1200 × 630 link-preview card as PNG bytes."""
    width, height = OG_SIZE
    image = Image.new("RGB", OG_SIZE, _BACKGROUND)
    draw = ImageDraw.Draw(image)

    quote = clean_text(card.quote).strip('“”"')
    show = clean_text(card.show)
    speaker = clean_text(card.speaker) if card.speaker else ""
    episode_title = clean_text(card.episode_title)

    footer_top = height - 104
    art_left = width - _MARGIN - _ARTWORK
    text_width = art_left - _MARGIN - 56

    # Artwork, top right.
    tile = _artwork_tile(card.artwork, show)
    image.paste(tile, (art_left, 96), tile)

    # Attribution, sized first so the quote gets the space that is left.
    name_font = _font(_SANS, 30, 650)
    plain_font = _font(_SANS, 30, 400)
    small_font = _font(_SANS, 22, 400)
    attribution_height = 30 + 14 + 22

    quote_top = 84
    quote_box = footer_top - 40 - attribution_height - 36 - quote_top
    quote_font, lines = _fit_quote(f"“{quote}”", text_width, quote_box)
    line_height = quote_font.size * _LINE_SPACING
    y = float(quote_top)
    for line in lines:
        draw.text((_MARGIN, y), line, font=quote_font, fill=_TEXT)
        y += line_height

    y += 30
    x = float(_MARGIN)
    if speaker:
        shown = _truncate(speaker, name_font, text_width * 0.6)
        draw.text((x, y), shown, font=name_font, fill=_TEXT)
        x += name_font.getlength(shown)
        draw.text((x, y), " on ", font=plain_font, fill=_MUTED)
        x += plain_font.getlength(" on ")
    else:
        draw.text((x, y), "A guest on ", font=plain_font, fill=_MUTED)
        x += plain_font.getlength("A guest on ")
    draw.text((x, y), _truncate(show, plain_font, _MARGIN + text_width - x), font=plain_font, fill=_ACCENT)
    y += 30 + 14
    if episode_title:
        draw.text((_MARGIN, y), _truncate(episode_title, small_font, text_width), font=small_font, fill=_MUTED)

    # Footer: the link as text, and the mark.
    draw.line((_MARGIN, footer_top, width - _MARGIN, footer_top), fill=_RULE, width=2)
    footer_y = footer_top + 52
    draw.text((_MARGIN, footer_y), clean_text(card.short_url), font=small_font, fill=_MUTED, anchor="lm")
    draw.text((width - _MARGIN, footer_y), "Thestill", font=_font(_SERIF, 34, 600), fill=_TEXT, anchor="rm")

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
