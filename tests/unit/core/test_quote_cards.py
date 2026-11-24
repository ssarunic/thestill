"""Spec #114 §3 — the quote card renderer."""

import io

from PIL import Image

from thestill.core.quote_cards import OG_SIZE, QuoteCard, _font, clean_text, render_og, wrap_balanced
from thestill.services.quote_share_service import card_eligible, share_id, stands_alone

QUOTE = "The best teams changed their minds fastest when the evidence changed."


def _card(**over) -> QuoteCard:
    values = dict(
        quote=QUOTE, speaker="Maya Okafor", show="The Long Margin", episode_title="Boring", short_url="t.me/q/x"
    )
    values.update(over)
    return QuoteCard(**values)


def _size(png: bytes):
    return Image.open(io.BytesIO(png)).size


def test_card_is_a_png_of_the_link_preview_size():
    png = render_og(_card())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert _size(png) == OG_SIZE


def test_card_renders_at_the_length_cap_without_a_speaker_and_with_other_scripts():
    longest = ("Čovjek kaže da je ovo vrlo duga rečenica. " * 8)[:320]
    assert _size(render_og(_card(quote=longest, speaker=None, show="Š" * 200, episode_title="E" * 400))) == OG_SIZE
    assert _size(render_og(_card(quote="اقتباس قصير من حلقة.", show="بودكاست"))) == OG_SIZE


def test_unreadable_artwork_falls_back_to_the_tile_and_real_artwork_is_used():
    plain = render_og(_card())
    assert render_og(_card(artwork=b"not an image")) == plain
    picture = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 30, 30)).save(picture, format="JPEG")
    assert render_og(_card(artwork=picture.getvalue())) != plain


def test_text_is_drawn_as_text_control_characters_removed():
    assert clean_text("a\x00b‮ c\n\td") == "ab c d"
    assert render_og(_card(quote="Line one.\x1b[31m\nLine two."))[:4] == b"\x89PNG"


def test_wrap_keeps_every_word_and_fits_the_width():
    font = _font("Newsreader.ttf", 40, 420)
    lines = wrap_balanced(QUOTE, font, 500)
    assert " ".join(lines) == QUOTE
    assert all(font.getlength(line) <= 500 for line in lines)
    # Balanced: the last line is not a lone word.
    assert len(lines[-1].split()) > 1


def test_a_word_wider_than_the_column_is_cut_not_drawn_over_the_artwork():
    font = _font("Newsreader.ttf", 40, 420)
    url = "https://example.com/" + "averyveryverylongpathsegment" * 6
    lines = wrap_balanced(f"See {url} for the details.", font, 500)
    assert all(font.getlength(line) <= 500 for line in lines)
    assert "".join(lines).replace(" ", "") == f"See{url}forthedetails."
    assert _size(render_og(_card(quote=f"See {url} for the details."))) == OG_SIZE


def test_what_may_become_a_card():
    assert card_eligible(QUOTE)
    assert not card_eligible("and then it trails in from a clause.")
    assert not card_eligible("It has no ending")
    assert not card_eligible("A" * 321 + ".")
    assert stands_alone("“Quoted and whole.”")


def test_share_id_is_stable_and_depends_on_the_words():
    first = share_id("ep", 1000, QUOTE)
    assert first == share_id("ep", 1000, QUOTE)
    assert len(first) == 10 and first.islower()
    assert (
        len({first, share_id("ep", 1000, QUOTE + " More."), share_id("ep", 2000, QUOTE), share_id("e2", 1000, QUOTE)})
        == 4
    )
