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

"""Shared ISO 639 language metadata for LLM prompts and artefact keys.

The :class:`~thestill.core.segmented_transcript_cleaner.SegmentedTranscriptCleaner`
needs name + spelling-rules vocabulary for its language-aware system
prompt. Keeping it in one place prevents drift as new languages are
added.
"""

import re
from typing import Dict, Optional, TypedDict


class LanguageSpec(TypedDict):
    """A single row of language metadata used in prompts."""

    name: str
    spelling: str


LANGUAGE_CONFIG: Dict[str, LanguageSpec] = {
    "en": {"name": "English", "spelling": "British English (e.g., 'labour', 'programme', 'realise', 'colour')"},
    "hr": {"name": "Croatian", "spelling": "standard Croatian spelling rules"},
    "de": {"name": "German", "spelling": "standard German orthography (Rechtschreibung)"},
    "es": {"name": "Spanish", "spelling": "standard Spanish spelling rules"},
    "fr": {"name": "French", "spelling": "standard French spelling rules"},
    "it": {"name": "Italian", "spelling": "standard Italian spelling rules"},
    "pt": {"name": "Portuguese", "spelling": "standard Portuguese spelling rules"},
    "nl": {"name": "Dutch", "spelling": "standard Dutch spelling rules"},
    "pl": {"name": "Polish", "spelling": "standard Polish spelling rules"},
    "ru": {"name": "Russian", "spelling": "standard Russian spelling rules"},
    "cs": {"name": "Czech", "spelling": "standard Czech spelling rules"},
    "sk": {"name": "Slovak", "spelling": "standard Slovak spelling rules"},
    "sl": {"name": "Slovenian", "spelling": "standard Slovenian spelling rules"},
    "sr": {"name": "Serbian", "spelling": "standard Serbian spelling rules"},
    "bs": {"name": "Bosnian", "spelling": "standard Bosnian spelling rules"},
    "uk": {"name": "Ukrainian", "spelling": "standard Ukrainian spelling rules"},
    "hu": {"name": "Hungarian", "spelling": "standard Hungarian spelling rules"},
    "ro": {"name": "Romanian", "spelling": "standard Romanian spelling rules"},
    "bg": {"name": "Bulgarian", "spelling": "standard Bulgarian spelling rules"},
    "el": {"name": "Greek", "spelling": "standard Greek spelling rules"},
    "sv": {"name": "Swedish", "spelling": "standard Swedish spelling rules"},
    "da": {"name": "Danish", "spelling": "standard Danish spelling rules"},
    "fi": {"name": "Finnish", "spelling": "standard Finnish spelling rules"},
    "no": {"name": "Norwegian", "spelling": "standard Norwegian spelling rules"},
    "ja": {"name": "Japanese", "spelling": "standard Japanese writing conventions"},
    "ko": {"name": "Korean", "spelling": "standard Korean writing conventions"},
    "zh": {"name": "Chinese", "spelling": "standard Chinese writing conventions"},
    "ar": {"name": "Arabic", "spelling": "standard Arabic writing conventions"},
    "tr": {"name": "Turkish", "spelling": "standard Turkish spelling rules"},
}

_LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2,3}$")


def normalize_language_code(language: Optional[str], *, default: str = "en") -> str:
    """Return a safe lower-case primary ISO language subtag.

    RSS and browser locales are often BCP-47 values (for example ``en-GB``),
    while summary filenames use only the primary language subtag. Invalid
    values fall back rather than ever reaching a storage path.
    """

    candidate = (language or "").strip().lower().replace("_", "-").split("-", 1)[0]
    if _LANGUAGE_CODE_RE.fullmatch(candidate):
        return candidate
    fallback = default.strip().lower().replace("_", "-").split("-", 1)[0]
    return fallback if _LANGUAGE_CODE_RE.fullmatch(fallback) else "en"


def resolve_language_spec(language: str) -> LanguageSpec:
    """Return the ``LanguageSpec`` for an ISO 639-1 code, with a safe fallback.

    Unknown codes yield a generic spec (uppercased code as name, generic
    spelling rules) so callers never crash on an exotic language.
    """
    language = normalize_language_code(language)
    spec = LANGUAGE_CONFIG.get(language)
    if spec is not None:
        return spec
    return {
        "name": language.upper(),
        "spelling": f"standard {language.upper()} spelling rules",
    }
