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

"""What a public page is called in search results.

An episode page used to be titled with the publisher's own episode title,
the same title Apple, Spotify, YouTube and every transcript site use, and
at about 105 characters Google cut it off before the show or the site
name. The searches that brought readers were a guest, a topic and a show
("louis theroux vinnie jones interview", "the rest is science placebo"), so
the title keeps those three and says what the page is, within about 600
pixels: "Vinnie Jones on The Louis Theroux Podcast — summary & key quotes",
"Something Weird Is Going On With Placebos — The Rest Is Science". Google
shows the site name on its own line, so the title does not repeat it.

A person page's description used to be a quote cut at 160 characters, often
filler from mid-sentence; it is now whole clauses about who they are and
where they are heard.

Both rules were judged against 45 real episodes and 15 real person pages
(Codex review, 2026-11-20): a guest is used only when the publisher names
them (guest lists carry extraction mistakes such as a court case stored as
a person), the topic is kept rather than dropped, nothing ends in a made-up
ellipsis, and a non-English show gets no English wrapper.

Pure functions, so the rules can be tested and judged without a server.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Iterable, List, Optional, Sequence, Tuple

#: Google cuts a title at about 600 pixels: 60-65 characters of mixed case.
TITLE_MAX = 65
DESCRIPTION_MAX = 160

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
# "Lenny's Podcast: Product | Career | Growth", "The Twenty Minute VC
# (20VC): Venture Capital | ...". A short suffix is part of the name
# ("The Rest Is Politics: US"), so only a long one or a list is a tagline.
_SHOW_TAGLINE_SPLIT_RE = re.compile(r"\s*(?:\(|:\s|\s[|–—-]\s)")
# Episode markers in front: "579. ", "#123: ", "Ep. 12 - ", "Episode 12: ",
# "Epizoda 223: ", "S10 EP22: ". A bare number with a colon is content
# ("1984: Orwell and surveillance"), so it stays.
_EPISODE_NUMBER_RE = re.compile(
    r"^\s*(?:s\d+\s*)?(?:"
    r"(?:ep(?:isode|izoda|isodio|isodi)?\.?|folge|épisode)\s*#?\d+[a-z]?\s*[.:|–—-]?\s*"
    r"|#\d+[a-z]?\s*[.:|–—-]?\s*"
    r"|\d+\.\s+)",
    re.I,
)
# Behind: "... | EP #252", "... - Episode 12". Never "COVID-19".
_TRAILING_NUMBER_RE = re.compile(r"(?:\s+[–—-]\s+|\s*\|\s*)(?:ep(?:isode)?\.?\s*#?|#)\d+\s*$", re.I)
# Where an episode title can be cut without leaving half a phrase: after a
# question or exclamation (kept), at a colon, a bar or a spaced dash; then,
# inside a segment, at a comma.
_SEGMENT_RE = re.compile(r"(?<=[?!])\s+|\s*[:|]\s+|\s+[–—+-]\s+")
_CLAUSE_END_RE = re.compile(r",\s")
# A credit, not a topic: "with Chris Manning and Fan-yun Sun", "ft. Ray Madoff".
_CREDIT_RE = re.compile(r"^(?:with|w/|ft\.?|feat\.?|featuring)\s", re.I)
_DASH_RE = re.compile(r"\s[–—-]\s")
# "Moonshots with Peter Diamandis" is "Moonshots" when space is short.
_WITH_HOST_RE = re.compile(r"\s+with\s+.+$", re.I)
_MIN_PHRASE = 15
# A speaker label stored as a person: "Unknown speaker", "Intro narrator".
_LABEL_NAME_RE = re.compile(r"^unknown\b|\b(?:narrator|announcer|voiceover|voice-over)\b", re.I)
_HONORIFIC_RE = re.compile(r"^(?:dr|prof|professor|sir|dame|lord|lady|mr|mrs|ms)\.?\s+", re.I)


def plain_text(text: Optional[str]) -> str:
    """Text without HTML tags or entities, on one line (a publisher's show
    description often arrives as ``<p>…</p>``)."""
    without_tags = _TAG_RE.sub(" ", text or "")
    return _SPACE_RE.sub(" ", html.unescape(without_tags)).strip()


def clip(text: str, limit: int) -> str:
    """``text`` cut at a word boundary to ``limit`` characters, with an
    ellipsis when anything was cut."""
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:—–-·|&/ ") + "…"


def _fold(text: str) -> str:
    """Lower case without accents, for matching a name in free text."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def short_show_name(title: str) -> str:
    """The show's name without the publisher's tagline. A suffix of one or
    two words is kept: it names an edition, not a slogan."""
    title = plain_text(title)
    parts = _SHOW_TAGLINE_SPLIT_RE.split(title, maxsplit=1)
    head = parts[0].strip()
    if len(parts) == 1 or len(head) < 3:
        return title
    rest = title[len(head) :].lstrip(" (:|–—-")
    if "|" not in rest and len(rest.split()) <= 2 and not title[len(head) :].lstrip().startswith("("):
        return title
    return head


def clean_episode_title(title: str) -> str:
    """The publisher's episode title without an episode number in front or
    behind."""
    plain = plain_text(title)
    cleaned = _TRAILING_NUMBER_RE.sub("", _EPISODE_NUMBER_RE.sub("", plain)).strip()
    return cleaned or plain


def _phrases(episode: str, show: str = "") -> List[str]:
    """``episode``, then, segment by segment in order (breaks: a question,
    a colon, a bar, a dash, a plus), the segment and then its leading
    clauses, longest first ("GPT-5.6, Government Staggers AI Model
    Releases"). Earlier parts win: the first segment usually carries the
    topic. Never a fragment shorter than ``_MIN_PHRASE``, a credit ("with
    ...") or the show's own name ("This Week in AI E11")."""
    segments = [part.strip() for part in _SEGMENT_RE.split(episode) if part and part.strip()]
    show_key = _fold(show)[:12] if len(show) >= 6 else ""
    phrases = [episode]
    for segment in segments:
        clauses = [c.strip() for c in _CLAUSE_END_RE.split(segment) if c.strip()]
        cuts = [segment] + [", ".join(clauses[:k]) for k in range(len(clauses) - 1, 0, -1)]
        for cut in cuts:
            if (
                len(cut) >= _MIN_PHRASE
                and cut not in phrases
                and not _CREDIT_RE.match(cut)
                and not (show_key and _fold(cut).startswith(show_key))
            ):
                phrases.append(cut)
    return phrases


def _show_names(show_title: str) -> List[str]:
    """The show's name, then without its "with <host>" tail if it has one."""
    show = short_show_name(show_title)
    without_host = _WITH_HOST_RE.sub("", show).strip()
    return [show] + ([without_host] if len(without_host) >= 3 and without_host != show else [])


def _sep(text: str) -> str:
    """The separator before the show: a dash, or a bar when the episode
    title already has a dash in it."""
    return " | " if _DASH_RE.search(text) else " — "


def _join(names: Sequence[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _named_in(name: str, text: str) -> bool:
    """True when ``text`` names the person: the full name as whole words,
    or the surname as a whole, capitalised word ("Thompson" in "Gut Health
    | Dr. Chris Thompson", but not "Sun" in "the sun" or "Ann" in "Annual")."""
    haystack = _strip_accents(text)
    full = _strip_accents(_HONORIFIC_RE.sub("", plain_text(name))).strip()
    if not full:
        return False
    if re.search(rf"(?<!\w){re.escape(full)}(?!\w)", haystack, re.I):
        return True
    parts = full.split()
    surname = parts[-1] if len(parts) >= 2 else ""
    return len(surname) >= 3 and re.search(rf"(?<!\w){re.escape(surname)}(?!\w)", haystack) is not None


def usable_guest_names(names: Iterable[Optional[str]], *, evidence: str = "") -> List[str]:
    """Guest names fit for a title: no blanks, speaker labels or repeats,
    and, when ``evidence`` (the episode's own title and description) is
    given, only guests it names."""
    seen, kept = set(), []
    for name in names:
        name = plain_text(name)
        if not name or _LABEL_NAME_RE.search(name) or name.lower() in seen:
            continue
        if evidence and not _named_in(name, evidence):
            continue
        seen.add(name.lower())
        kept.append(name)
    return kept


def episode_search_title(
    episode_title: str,
    show_title: str,
    guest_names: Iterable[Optional[str]] = (),
    *,
    description: str = "",
    language: Optional[str] = "en",
    limit: int = TITLE_MAX,
) -> str:
    """The best title for one episode, ignoring the show's other episodes
    (see :func:`assign_unique_titles` for those)."""
    return episode_title_candidates(
        episode_title, show_title, guest_names, description=description, language=language, limit=limit
    )[0]


def episode_title_candidates(
    episode_title: str,
    show_title: str,
    guest_names: Iterable[Optional[str]] = (),
    *,
    description: str = "",
    language: Optional[str] = "en",
    limit: int = TITLE_MAX,
) -> List[str]:
    """Every title that fits ``limit``, best first, then the whole wording.
    The order, trying the show's full name
    first and then its name without "with <host>":

    1. guest, topic and show: ``<Episode title> — <Show>`` when the whole
       title names every guest and says more than their names, else
       ``<Guest>: <part of the title that does not name them> — <Show>``,
       with every show name;
    2. guest and show: ``<Guest> on <Show> — summary & key quotes``, then
       shorter forms;
    3. topic and show: ``<Episode title> — <Show> summary``, then
       ``<Episode title> — <Show>``.

    The episode title may be cut at a natural break (``_phrases``), never
    mid-phrase: when nothing fits, the whole title and the shortest show
    name are returned over the limit, and the result page cuts them. A
    guest counts only when the
    publisher's title or description names them. A show whose language is
    not English gets no English words.
    """
    episode = clean_episode_title(episode_title)
    phrases = _phrases(episode, short_show_name(show_title))
    english = (language or "en").lower().startswith("en")
    guests = usable_guest_names(guest_names, evidence=f"{episode_title} {plain_text(description)}")
    groups = ([guests[:2], guests[:1]] if len(guests) >= 2 else [guests[:1]]) if guests else []

    def says_more_than(text: str, names: Sequence[str]) -> bool:
        """True when ``text`` has a dozen letters besides every guest's name
        and surname ("Fetterman & McCormick" says nothing more)."""
        rest = _fold(text)
        for name in [*names, *guests]:
            full = _fold(_HONORIFIC_RE.sub("", name))
            rest = rest.replace(full, " ")
            if len(full.split()) >= 2:
                rest = re.sub(rf"\b{re.escape(full.split()[-1])}\b", " ", rest)
        return len(re.sub(r"[^a-z0-9]", "", rest)) >= 12

    shows = _show_names(show_title)
    candidates: List[str] = []
    # Guest, topic and show, before anything drops one. An earlier part of
    # the title with a shorter show name beats a later part with the full one.
    for names in groups:
        who = " & ".join(names)
        # The whole title when it names them; a part of it that names them
        # is usually a credential ("Dylan Patel of SemiAnalysis").
        if all(_named_in(g, episode) for g in names) and says_more_than(episode, names):
            candidates += [f"{episode}{_sep(episode)}{show}" for show in shows]
        for phrase in phrases:
            if not any(_named_in(g, phrase) for g in names):
                candidates += [f"{who}: {phrase}{_sep(phrase)}{show}" for show in shows]
    # Then guest and show: the guest's name is what most searches were.
    # Both guests with every show name before the second guest goes.
    for names in groups:
        for show in shows:
            who = " & ".join(names)
            if english:
                candidates += [f"{who} on {show} — summary & key quotes", f"{who} on {show} — summary"]
                candidates.append(f"{who} on {show}")
            else:
                candidates.append(f"{who} — {show}")
    # Then topic and show, earliest part of the title first.
    for phrase in phrases:
        for show in shows:
            if english:
                candidates.append(f"{phrase}{_sep(phrase)}{show} summary")
            candidates.append(f"{phrase}{_sep(phrase)}{show}")
    # Then the topic alone, when the show's name is too long to fit beside it.
    for phrase in phrases[1:]:
        candidates.append(f"{phrase} — summary" if english else phrase)
    fitting = [c for c in dict.fromkeys(candidates) if len(c) <= limit]
    # Last: the publisher's whole wording with the shortest show name, over
    # the limit, for the result page to cut, rather than a made-up fragment.
    whole = f"{episode}{_sep(episode)}{shows[-1]}"
    return fitting + ([whole] if whole not in fitting else [])


def assign_unique_titles(episodes: Sequence[Tuple[str, Sequence[str], str]]) -> dict:
    """``{key: title}`` with no two episodes of a show sharing a title.

    ``episodes`` are ``(key, candidates, date_label)``, oldest first: each
    takes its best candidate an older episode has not taken, so a title
    stays put when newer episodes arrive. A recurring guest without a
    topic that fits ("Ranjan Roy on Big Technology Podcast") or a series cut
    to its shared name ("Dawn of the Samurai") moves on to its next
    candidate; when every one is taken, the best is dated.
    """
    used: set = set()
    titles = {}
    for key, candidates, date_label in episodes:
        title = next((c for c in candidates if _title_key(c) not in used), None)
        if title is None:
            base = candidates[0] if candidates else key
            title = f"{base} ({date_label})" if date_label else base
            suffix = 2
            while _title_key(title) in used:
                title = f"{base} ({date_label or ''} {suffix})".replace("( ", "(")
                suffix += 1
        used.add(_title_key(title))
        titles[key] = title
    return titles


_BOILERPLATE_RE = re.compile(r"\b(?:summary|key quotes)\b|[—|&·:–-]")


def _title_key(title: str) -> str:
    """What makes two titles the same to a reader: the words, without the
    "summary & key quotes" wrapper or separators. "Marc Andreessen on The
    a16z Show — summary" and "... — summary & key quotes" are one title."""
    return " ".join(_BOILERPLATE_RE.sub(" ", _fold(title)).split())


def _usable_headline(headline: Optional[str]) -> str:
    """A short credential ("American psychologist"), or nothing: a long one
    or one with slashes crowds out where the person is heard."""
    headline = plain_text(headline)
    if not headline or "/" in headline or len(headline) > 45:
        return ""
    return headline


def person_search_description(
    name: str,
    *,
    headline: Optional[str] = None,
    hosts: Sequence[str] = (),
    recurring: Sequence[str] = (),
    guest_on: Sequence[str] = (),
    episodes: int = 0,
    limit: int = DESCRIPTION_MAX,
) -> str:
    """Whole clauses, longest first, the first that fits ``limit``:
    ``<Name>, <credential>, host of / regular on / guest on <shows>.
    Summaries and quotes from <n> episodes.`` No topic list: the topics
    mined from quotes ("Father and mother", "Culture") read as noise."""
    credential = _usable_headline(headline)

    def shows(titles: Sequence[str]) -> List[str]:
        return list(dict.fromkeys(short_show_name(s) for s in titles if s))[:2]

    roles: List[str] = []
    for label, titles in (("host of", hosts), ("regular on", recurring), ("guest on", guest_on)):
        named = shows(titles)
        if named:
            roles = [f"{label} {_join(named)}", f"{label} {named[0]}"] if len(named) > 1 else [f"{label} {named[0]}"]
            break
    role = roles[0] if roles else ""
    if episodes > 1:
        reading = f"Summaries and quotes from {episodes} episodes"
    elif episodes == 1 or role:
        reading = "Read the episode summary and key quotes"
    else:
        reading = ""

    def sentence(*parts: str) -> str:
        return ", ".join(p for p in parts if p) + "."

    candidates = []
    for one_role in roles or [""]:
        for with_credential in (True, False):
            lead = sentence(name, credential if with_credential else "", one_role)
            if reading:
                candidates.append(f"{lead} {reading}.")
            candidates.append(lead)
    # A role too long to fit at all: the name and what the page holds.
    candidates += [
        f"{sentence(name, credential)} {reading}." if reading else "",
        f"{name}. {reading}." if reading else "",
    ]
    for candidate in candidates:
        if candidate and len(candidate) <= limit:
            return candidate
    return clip(f"{name}.", limit)


def lead_with_full_name(description: str, guests: Sequence[str], *, limit: int = DESCRIPTION_MAX) -> str:
    """The summary's first sentence, with a leading first name made the
    guest's full name: "Koray argues that ..." means nothing to someone
    searching; "Koray Kavukcuoglu argues that ..." does. Unchanged when the
    first word is not a guest's first name, the summary already leads with
    the full name, or the longer one does not fit."""
    words = description.split(" ", 1)
    if len(words) < 2:
        return description
    first, rest = words
    match = re.match(r"^(.+?)(’s|'s|,)?$", first)
    first_bare, ending = match.group(1), match.group(2) or ""
    for name in guests:
        parts = _HONORIFIC_RE.sub("", plain_text(name)).split()
        if len(parts) >= 2 and parts[0] == first_bare:
            leading = description.split()[: len(parts)]
            if leading[:-1] == parts[:-1] and re.match(rf"^{re.escape(parts[-1])}(’s|'s|,)?$", leading[-1]):
                return description  # already the full name
            full = " ".join(parts) + ending
            longer = f"{full} {rest}"
            return longer if len(longer) <= limit else description
    return description


# A sentence ends at . ! ? followed by a space and a capital, digit or
# opening quote; not after an initial or a title ("M.G. Siegler", "Dr. Chris").
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'“‘(]?[A-Z0-9])")
_ABBREVIATION_END_RE = re.compile(
    r"(?:\b(?:Dr|Mr|Mrs|Ms|Prof|St|Sr|Jr|vs|etc|Inc|Ltd|Co|No|Mt|Gen|Sen|Rep|Gov)|\b[A-Z](?:\.[A-Z])*)\.$"
)
#: Shorter than this reads as thin in a result (Bing flags "too short").
DESCRIPTION_MIN = 110


def lead_sentences(text: Optional[str], *, min_len: int = DESCRIPTION_MIN, limit: int = DESCRIPTION_MAX) -> str:
    """Whole sentences from the start of ``text``: the first, then the next
    while the result is under ``min_len`` and still fits ``limit``. An
    initial or a title never ends a sentence, so "M.G. Siegler joins..."
    is not cut to "M.G.". A first sentence longer than ``limit`` is cut at
    a word."""
    text = plain_text(text)
    if not text:
        return ""
    sentences: List[str] = []
    for piece in _SENTENCE_END_RE.split(text):
        if sentences and _ABBREVIATION_END_RE.search(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {piece}"
        else:
            sentences.append(piece)
    result = ""
    for sentence in sentences:
        candidate = f"{result} {sentence}".strip()
        if len(candidate) > limit:
            break
        result = candidate
        if len(result) >= min_len:
            break
    return result or clip(sentences[0], limit)
