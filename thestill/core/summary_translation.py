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

"""Faithful Markdown summary translation for spec #58."""

import re
from typing import Iterable

from thestill.utils.language_config import normalize_language_code, resolve_language_spec
from thestill.utils.prompt_safety import UNTRUSTED_CONTENT_PREAMBLE, wrap_untrusted

from .llm_provider import MODEL_CONFIGS, LLMProvider


class SummaryTranslator:
    """Translate a generated summary without changing its structure or links."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def translate(self, markdown: str, *, target_language: str, source_language: str) -> str:
        target = normalize_language_code(target_language)
        source = normalize_language_code(source_language)
        target_name = resolve_language_spec(target)["name"]
        source_name = resolve_language_spec(source)["name"]
        system_prompt = (
            f"""You are a precise translator of podcast summaries.
Translate the complete Markdown document from {source_name} into {target_name}.
Translate all prose and section headings, but preserve the meaning and tone.

These constraints are mandatory:
* Preserve the Markdown structure, numbering, emojis, indentation, and bullet markers.
* Preserve timestamps and proper names unless the target language has a conventional form.
* Preserve every Markdown link destination byte-for-byte, especially destinations
  containing `?t=` and `cite=`.
* Preserve any `[[...]]` syntax byte-for-byte.
* Do not add a preamble, explanation, or Markdown fence.
"""
            + UNTRUSTED_CONTENT_PREAMBLE
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "SUMMARY TO TRANSLATE:\n\n" + wrap_untrusted(markdown, label="SUMMARY"),
            },
        ]
        limits = MODEL_CONFIGS.get(self.provider.get_model_name())
        temperature = 0.2 if limits is None or limits.supports_temperature else None
        return self.provider.chat_completion(messages=messages, temperature=temperature)

    def detect_language(self, markdown: str, *, candidates: Iterable[str]) -> str:
        """Identify a legacy canonical summary language from a small candidate set."""

        normalized = list(dict.fromkeys(normalize_language_code(code) for code in candidates))
        if not normalized:
            return "en"
        if len(normalized) == 1:
            return normalized[0]
        descriptions = ", ".join(f"{code} ({resolve_language_spec(code)['name']})" for code in normalized)
        system_prompt = (
            f"""Identify the primary language of the supplied podcast summary.
The only valid answers are: {descriptions}.
Reply with exactly one language code and nothing else.
"""
            + UNTRUSTED_CONTENT_PREAMBLE
        )
        # A representative prefix is enough for language identification and
        # keeps this one-time compatibility call cheap for long summaries.
        sample = markdown[:6000]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": wrap_untrusted(sample, label="SUMMARY")},
        ]
        limits = MODEL_CONFIGS.get(self.provider.get_model_name())
        temperature = 0.0 if limits is None or limits.supports_temperature else None
        response: str = self.provider.chat_completion(messages=messages, temperature=temperature).strip().lower()
        detected_codes: list[str] = re.findall(r"\b[a-z]{2,3}\b", response)
        for code in detected_codes:
            if code in normalized:
                return code
        return "en" if "en" in normalized else normalized[0]
