#!/usr/bin/env python3
"""
Prototype: Compare transcript cleaning approaches.

Approach B: Corrections-list with fuzzy matching
Approach C: Deterministic structure + targeted LLM corrections
Approach D: Current full-rewrite pipeline (TranscriptCleaner)

All compared against existing cleaned text (reference).

Usage:
    ./venv/bin/python scripts/cleaning_prototype.py
"""

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from thestill.core.facts_manager import FactsManager
from thestill.core.llm_provider import create_llm_provider_from_config
from thestill.core.transcript_cleaner import TranscriptCleaner
from thestill.core.transcript_formatter import TranscriptFormatter
from thestill.models.facts import EpisodeFacts, PodcastFacts
from thestill.utils.config import load_config
from thestill.utils.path_manager import PathManager

# ---------------------------------------------------------------------------
# Test episodes
# ---------------------------------------------------------------------------
TEST_EPISODES = [
    {
        "podcast_slug": "lenny-s-podcast-product-career-growth",
        "episode_slug": "the-high-growth-handbook-molly-grahams-frameworks-for-leading-through-chaos-change-and-scale",
        "label": "PROBLEMATIC-1 (Lenny/Molly Graham)",
    },
    {
        "podcast_slug": "the-artificial-intelligence-show",
        "episode_slug": "194-agentic-ai-timelines-generalists-vs-specialists-resume-tips-ai-learning-ownership-handling-model",
        "label": "PROBLEMATIC-2 (AI Show #194)",
    },
    {
        "podcast_slug": "how-i-ai",
        "episode_slug": "pms-who-use-ai-will-replace-those-who-dont-googles-ai-product-lead-on-the-new-pm-toolkit-marily",
        "label": "NORMAL-1 (How I AI / Google PM)",
    },
    {
        "podcast_slug": "the-pragmatic-engineer",
        "episode_slug": "how-ai-will-change-software-engineering-with-martin-fowler",
        "label": "NORMAL-2 (Pragmatic Engineer / Fowler)",
    },
    {
        "podcast_slug": "the-mad-podcast-with-matt-turck",
        "episode_slug": "everything-gets-rebuilt-the-new-ai-agent-stack-harrison-chase-langchain",
        "label": "PROBLEMATIC-3 (MAD/LangChain)",
    },
    {
        "podcast_slug": "what-s-cooking-a-podcast-from-nory",
        "episode_slug": "uk-budget-deep-dive-with-kate-nicholls-higher-costs-zero-relief",
        "label": "PROBLEMATIC-4 (Nory/UK Budget)",
    },
    {
        "podcast_slug": "the-twenty-minute-vc-20vc-venture-capital-startup-funding-the-pitch",
        "episode_slug": "20vc-50-of-funds-will-go-out-of-business-why-growth-expectations-today-are-bs-and-will-not-last-why",
        "label": "PROBLEMATIC-5 (20VC/Funds)",
    },
    {
        "podcast_slug": "the-rest-is-science",
        "episode_slug": "how-to-drink-lava",
        "label": "NORMAL-3 (Rest is Science/Lava)",
    },
    {
        "podcast_slug": "masters-of-scale",
        "episode_slug": "reid-hoffman-inflection-ais-sean-white-on-designing-ai-that-makes-us-better-humans",
        "label": "NORMAL-4 (Masters of Scale/Hoffman)",
    },
    {
        "podcast_slug": "how-i-ai",
        "episode_slug": "how-zapiers-ea-built-an-army-of-ai-interns-to-automate-meeting-prep-strengthen-team-culture-and-scal",
        "label": "NORMAL-5 (How I AI/Zapier)",
    },
    {
        "podcast_slug": "the-twenty-minute-vc-20vc-venture-capital-startup-funding-the-pitch",
        "episode_slug": "20vc-0-260m-in-revenue-in-three-years-how-we-did-it-you-need-to-work-weekends-to-win-most-founders-a",
        "label": "NORMAL-6 (20VC/$260M Revenue)",
    },
    {
        "podcast_slug": "the-artificial-intelligence-show",
        "episode_slug": "188-ai-trends-for-2026-google-deepmind-ai-predictions-gemini-3-flash-ai-world-models-are-ai-job-loss",
        "label": "NORMAL-7 (AI Show #188)",
    },
    {
        "podcast_slug": "the-twenty-minute-vc-20vc-venture-capital-startup-funding-the-pitch",
        "episode_slug": "20vc-brex-acquired-for-5-15bn-a16z-companies-are-2-3-ai-revenues-anthropic-inference-costs-skyrocket",
        "label": "NORMAL-8 (20VC/Brex)",
    },
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class CleaningResult:
    approach: str
    episode_label: str
    output_text: str
    first_timestamp_sec: float
    duration_sec: float
    llm_calls: int
    input_tokens_approx: int
    output_tokens_approx: int
    content_retention: float
    corrections_count: int = 0
    corrections_detail: List = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_first_timestamp(text: str) -> float:
    match = re.search(r"\[(\d+):(\d+):(\d+)\]|\[(\d+):(\d+)\]", text)
    if not match:
        return -1
    if match.group(1) is not None:
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
    return int(match.group(4)) * 60 + int(match.group(5))


def compute_content_retention(raw_segments: List[Dict], cleaned_text: str) -> float:
    raw_words = []
    for seg in raw_segments:
        raw_words.extend(seg.get("text", "").lower().split())
    if not raw_words:
        return 0.0
    cleaned_lower = cleaned_text.lower()
    found = sum(1 for w in raw_words if w in cleaned_lower)
    return found / len(raw_words)


def approx_tokens(text: str) -> int:
    return len(text) // 4


def load_test_data(episode: Dict, facts_manager: FactsManager) -> Optional[Dict]:
    podcast_slug = episode["podcast_slug"]
    episode_slug = episode["episode_slug"]

    raw_dir = Path("data/raw_transcripts") / podcast_slug
    raw_files = list(raw_dir.glob(f"{episode_slug}*_transcript.json"))
    if not raw_files:
        print(f"  [SKIP] No raw transcript for {episode['label']}")
        return None

    with open(raw_files[0]) as f:
        raw_data = json.load(f)

    clean_dir = Path("data/clean_transcripts") / podcast_slug
    clean_files = list(clean_dir.glob(f"{episode_slug}*_cleaned.md"))
    existing_cleaned = ""
    if clean_files:
        existing_cleaned = clean_files[0].read_text()

    podcast_facts = facts_manager.load_podcast_facts(podcast_slug)
    episode_facts = facts_manager.load_episode_facts(podcast_slug, episode_slug)

    if not episode_facts:
        print(f"  [SKIP] No episode facts for {episode['label']}")
        return None

    return {
        "raw_data": raw_data,
        "existing_cleaned": existing_cleaned,
        "podcast_facts": podcast_facts,
        "episode_facts": episode_facts,
        "podcast_slug": podcast_slug,
        "episode_slug": episode_slug,
    }


def extract_json(response: str) -> str:
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    if match:
        return match.group(1).strip()
    response = response.strip()
    if response.startswith("[") or response.startswith("{"):
        return response
    return response


def build_facts_context(podcast_facts, episode_facts):
    """Build facts context lines for prompts."""
    lines = []
    if podcast_facts:
        lines.append("PODCAST FACTS:")
        if podcast_facts.hosts:
            lines.append(f"Hosts: {', '.join(podcast_facts.hosts)}")
        if podcast_facts.sponsors:
            lines.append(f"Known Sponsors: {', '.join(podcast_facts.sponsors)}")
        if podcast_facts.keywords:
            lines.append(f"Keywords & Mishearings: {', '.join(podcast_facts.keywords)}")
        lines.append("")
    lines.append("EPISODE FACTS:")
    lines.append(f"Episode Title: {episode_facts.episode_title}")
    if episode_facts.guests:
        lines.append(f"Guests: {', '.join(episode_facts.guests)}")
    if episode_facts.topics_keywords:
        lines.append(f"Topics: {', '.join(episode_facts.topics_keywords)}")
    if episode_facts.ad_sponsors:
        lines.append(f"Ad Sponsors: {', '.join(episode_facts.ad_sponsors)}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Existing cleaned text reference
# ---------------------------------------------------------------------------
def evaluate_existing(data: Dict, episode: Dict) -> CleaningResult:
    raw_segments = data["raw_data"].get("segments", [])
    cleaned = data["existing_cleaned"]
    return CleaningResult(
        approach="Existing cleaned",
        episode_label=episode["label"],
        output_text=cleaned,
        first_timestamp_sec=parse_first_timestamp(cleaned),
        duration_sec=0,
        llm_calls=0,
        input_tokens_approx=0,
        output_tokens_approx=approx_tokens(cleaned),
        content_retention=compute_content_retention(raw_segments, cleaned),
    )


# ---------------------------------------------------------------------------
# APPROACH B: Corrections list with fuzzy matching
# ---------------------------------------------------------------------------
CORRECTIONS_SYSTEM_PROMPT = """You are an expert podcast transcript editor. You will receive a formatted transcript and must output ONLY a JSON array of corrections.

DO NOT rewrite the transcript. Instead, identify specific errors and output corrections in this exact JSON format:

```json
[
  {
    "timestamp": "[MM:SS]",
    "type": "entity|ad|filler|grammar|clip",
    "find": "exact text to find near this timestamp (10-50 words of context)",
    "replace": "corrected text",
    "reason": "brief reason"
  }
]
```

CORRECTION TYPES:

1. **entity**: Fix misspelled proper nouns using the Keywords list.
   - find: include enough surrounding words for unique matching
   - replace: same text with the corrected name

2. **ad**: Mark sponsor reads as ad breaks.
   - find: the full ad/sponsor text
   - replace: "> **[TIMESTAMP] [AD BREAK]** - Sponsor Name"

3. **clip**: Label cold-open clips or soundbites.
   - find: the clip text
   - replace: the text prefixed with "**[Clip]** " or "**[Soundbite]** "

4. **filler**: Remove excessive filler words (um, uh, you know) only when they hurt readability.
   - find: text with fillers
   - replace: text without fillers

5. **grammar**: Fix obvious transcription errors only.
   - find: text with error
   - replace: corrected text

CRITICAL RULES:
- Output ONLY the JSON array, nothing else
- The "find" field must be VERBATIM text from the input (copy-paste, don't paraphrase)
- Include 10-50 words of context in "find" to ensure unique matching
- Include the nearest timestamp for anchoring
- Do NOT correct style, eloquence, or restructure sentences
- Do NOT add content that wasn't there
- If there are no corrections needed, output: []
- Aim for HIGH PRECISION: only flag things you're confident are errors
"""


def apply_corrections_fuzzy(text: str, corrections: List[Dict], threshold: float = 0.75) -> Tuple[str, int, List[Dict]]:
    """Apply corrections using fuzzy matching. Returns (corrected_text, applied_count, applied_details)."""
    applied = 0
    applied_details = []

    for corr in corrections:
        find_text = corr.get("find", "")
        replace_text = corr.get("replace", "")
        timestamp = corr.get("timestamp", "")

        if not find_text or not replace_text:
            continue
        if find_text == replace_text:
            continue

        matched = False
        match_context_before = ""
        match_context_after = ""

        # Try exact match first
        pos = text.find(find_text)
        if pos >= 0:
            # Grab surrounding sentence context
            ctx_start = max(0, text.rfind("\n", 0, pos))
            ctx_end = text.find("\n", pos + len(find_text))
            if ctx_end < 0:
                ctx_end = min(len(text), pos + len(find_text) + 200)
            match_context_before = text[ctx_start:ctx_end].strip()

            text = text[:pos] + replace_text + text[pos + len(find_text) :]

            ctx_end2 = text.find("\n", pos + len(replace_text))
            if ctx_end2 < 0:
                ctx_end2 = min(len(text), pos + len(replace_text) + 200)
            match_context_after = text[ctx_start:ctx_end2].strip()

            applied += 1
            matched = True
        else:
            # Fuzzy match
            search_start = 0
            if timestamp:
                ts_clean = timestamp.strip("[]")
                for ts_variant in [f"[{ts_clean}]", timestamp]:
                    ts_pos = text.find(ts_variant)
                    if ts_pos >= 0:
                        search_start = max(0, ts_pos - 200)
                        break

            window_size = max(len(find_text) * 3, 2000)
            search_end = min(len(text), search_start + window_size)
            search_window = text[search_start:search_end]

            best_ratio = 0
            best_start = -1
            best_end = -1
            find_len = len(find_text)

            for scale in [1.0, 0.95, 1.05, 0.9, 1.1]:
                window_len = int(find_len * scale)
                for i in range(0, len(search_window) - window_len + 1, 10):
                    candidate = search_window[i : i + window_len]
                    ratio = SequenceMatcher(None, find_text.lower(), candidate.lower()).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_start = i
                        best_end = i + window_len

            if best_ratio >= threshold and best_start >= 0:
                abs_start = search_start + best_start
                abs_end = search_start + best_end

                ctx_start = max(0, text.rfind("\n", 0, abs_start))
                ctx_end = text.find("\n", abs_end)
                if ctx_end < 0:
                    ctx_end = min(len(text), abs_end + 200)
                match_context_before = text[ctx_start:ctx_end].strip()

                text = text[:abs_start] + replace_text + text[abs_end:]

                ctx_end2 = text.find("\n", abs_start + len(replace_text))
                if ctx_end2 < 0:
                    ctx_end2 = min(len(text), abs_start + len(replace_text) + 200)
                match_context_after = text[ctx_start:ctx_end2].strip()

                applied += 1
                matched = True

        if matched:
            applied_details.append(
                {
                    **corr,
                    "before_context": match_context_before,
                    "after_context": match_context_after,
                }
            )

    return text, applied, applied_details


def approach_b_corrections(data: Dict, episode: Dict, provider, formatter: TranscriptFormatter) -> CleaningResult:
    start_time = time.time()
    raw_data = data["raw_data"]
    podcast_facts = data["podcast_facts"]
    episode_facts = data["episode_facts"]

    formatted = formatter.format_transcript(raw_data)
    cleaner = TranscriptCleaner(provider)
    formatted = cleaner._apply_speaker_mapping(formatted, episode_facts)

    facts_ctx = build_facts_context(podcast_facts, episode_facts)
    user_prompt = facts_ctx + "\nTRANSCRIPT TO ANALYZE:\n" + formatted

    messages = [
        {"role": "system", "content": CORRECTIONS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    input_tokens = approx_tokens(CORRECTIONS_SYSTEM_PROMPT + user_prompt)

    max_input_chars = 200000
    if len(user_prompt) > max_input_chars:
        chunks = []
        paragraphs = formatted.split("\n\n")
        current_chunk, current_size = [], 0
        chunk_size = max_input_chars - len(facts_ctx) - 1000
        for para in paragraphs:
            if current_size + len(para) > chunk_size and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk, current_size = [para], len(para)
            else:
                current_chunk.append(para)
                current_size += len(para)
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        all_corrections, llm_calls, total_output_tokens = [], 0, 0
        for i, chunk in enumerate(chunks):
            chunk_prompt = facts_ctx + f"\nTRANSCRIPT TO ANALYZE (Part {i+1}/{len(chunks)}):\n" + chunk
            resp = provider.chat_completion(
                messages=[
                    {"role": "system", "content": CORRECTIONS_SYSTEM_PROMPT},
                    {"role": "user", "content": chunk_prompt},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            llm_calls += 1
            total_output_tokens += approx_tokens(resp)
            try:
                parsed = json.loads(extract_json(resp))
                if isinstance(parsed, list):
                    all_corrections.extend(parsed)
                elif isinstance(parsed, dict) and "corrections" in parsed:
                    all_corrections.extend(parsed["corrections"])
            except (json.JSONDecodeError, KeyError) as e:
                print(f"    [WARN] Chunk {i+1} parse error: {e}")
        corrections = all_corrections
    else:
        resp = provider.chat_completion(messages=messages, temperature=0, response_format={"type": "json_object"})
        llm_calls = 1
        total_output_tokens = approx_tokens(resp)
        try:
            parsed = json.loads(extract_json(resp))
            corrections = parsed if isinstance(parsed, list) else parsed.get("corrections", [])
        except (json.JSONDecodeError, KeyError):
            corrections = []

    result_text, applied_count, applied_details = apply_corrections_fuzzy(formatted, corrections)
    elapsed = time.time() - start_time

    return CleaningResult(
        approach="B: Corrections list",
        episode_label=episode["label"],
        output_text=result_text,
        first_timestamp_sec=parse_first_timestamp(result_text),
        duration_sec=elapsed,
        llm_calls=llm_calls,
        input_tokens_approx=input_tokens,
        output_tokens_approx=total_output_tokens,
        content_retention=compute_content_retention(data["raw_data"].get("segments", []), result_text),
        corrections_count=applied_count,
        corrections_detail=applied_details[:20],
    )


# ---------------------------------------------------------------------------
# APPROACH C: Deterministic structure + targeted LLM for entities/ads
# ---------------------------------------------------------------------------
ENTITY_FIX_PROMPT = """You are a transcript entity fixer. Given a list of text segments and a vocabulary of known entities, output a JSON array of corrections for misspelled proper nouns ONLY.

Output format:
```json
[
  {"find": "misspelled word or phrase", "replace": "correct spelling", "segment_index": 0}
]
```

RULES:
- Only fix proper nouns (names, companies, places, technical terms)
- The "find" must be a SINGLE word or short phrase (1-4 words), not a full sentence
- Only fix words that are clearly wrong based on the vocabulary provided
- If uncertain, do NOT include a correction
- Output [] if no corrections needed
"""

AD_DETECT_PROMPT = """You are an ad break detector for podcast transcripts. Given a list of text segments with timestamps, identify which segments are sponsor reads / advertisements.

Output a JSON array of ad break ranges:
```json
[
  {"start_index": 5, "end_index": 8, "sponsor": "Sponsor Name"}
]
```

RULES:
- Only flag clear sponsor reads (promo codes, "visit X.com", "support comes from")
- Include the segment indices that are part of the ad
- Output [] if no ads detected
"""


def approach_c_hybrid(data: Dict, episode: Dict, provider, formatter: TranscriptFormatter) -> CleaningResult:
    start_time = time.time()
    raw_data = data["raw_data"]
    podcast_facts = data["podcast_facts"]
    episode_facts = data["episode_facts"]

    formatted = formatter.format_transcript(raw_data)
    cleaner = TranscriptCleaner(provider)
    formatted = cleaner._apply_speaker_mapping(formatted, episode_facts)

    segments = [s.strip() for s in formatted.split("\n\n") if s.strip()]

    vocabulary = []
    if podcast_facts:
        vocabulary.extend(podcast_facts.hosts or [])
        vocabulary.extend(podcast_facts.keywords or [])
        vocabulary.extend(podcast_facts.sponsors or [])
    if episode_facts:
        vocabulary.extend(episode_facts.guests or [])
        vocabulary.extend(episode_facts.topics_keywords or [])
        vocabulary.extend(episode_facts.ad_sponsors or [])

    llm_calls, total_input_tokens, total_output_tokens, total_corrections = 0, 0, 0, 0
    all_applied_details = []

    # Entity fixing in batches
    batch_size = 30
    for batch_start in range(0, len(segments), batch_size):
        batch = segments[batch_start : batch_start + batch_size]
        batch_data = [{"index": batch_start + i, "text": seg} for i, seg in enumerate(batch)]

        user_prompt = f"VOCABULARY (correct spellings):\n{json.dumps(vocabulary)}\n\nSEGMENTS:\n{json.dumps(batch_data, indent=2)}"
        msgs = [{"role": "system", "content": ENTITY_FIX_PROMPT}, {"role": "user", "content": user_prompt}]
        total_input_tokens += approx_tokens(ENTITY_FIX_PROMPT + user_prompt)

        resp = provider.chat_completion(messages=msgs, temperature=0, response_format={"type": "json_object"})
        llm_calls += 1
        total_output_tokens += approx_tokens(resp)

        try:
            parsed = json.loads(extract_json(resp))
            entity_corrections = parsed if isinstance(parsed, list) else parsed.get("corrections", [])
        except (json.JSONDecodeError, KeyError):
            entity_corrections = []

        for corr in entity_corrections:
            find, replace = corr.get("find", ""), corr.get("replace", "")
            idx = corr.get("segment_index", -1)
            if not find or not replace or idx < 0 or idx >= len(segments):
                continue
            if find in segments[idx]:
                before_ctx = segments[idx][:200]
                segments[idx] = segments[idx].replace(find, replace, 1)
                after_ctx = segments[idx][:200]
                total_corrections += 1
                all_applied_details.append(
                    {
                        "type": "entity",
                        "find": find,
                        "replace": replace,
                        "before_context": before_ctx,
                        "after_context": after_ctx,
                    }
                )
            else:
                # Fuzzy within segment
                seg_text = segments[idx]
                find_len = len(find)
                best_ratio, best_pos = 0, -1
                for i in range(0, len(seg_text) - find_len + 1):
                    r = SequenceMatcher(None, find.lower(), seg_text[i : i + find_len].lower()).ratio()
                    if r > best_ratio:
                        best_ratio, best_pos = r, i
                if best_ratio >= 0.8 and best_pos >= 0:
                    before_ctx = segments[idx][:200]
                    segments[idx] = seg_text[:best_pos] + replace + seg_text[best_pos + find_len :]
                    after_ctx = segments[idx][:200]
                    total_corrections += 1
                    all_applied_details.append(
                        {
                            "type": "entity",
                            "find": find,
                            "replace": replace,
                            "before_context": before_ctx,
                            "after_context": after_ctx,
                        }
                    )

    # Ad detection
    ad_context = [{"index": i, "preview": seg[:150]} for i, seg in enumerate(segments)]
    known_sponsors = (podcast_facts.sponsors if podcast_facts else []) + (
        episode_facts.ad_sponsors if episode_facts else []
    )
    ad_prompt = f"KNOWN SPONSORS: {json.dumps(known_sponsors)}\n\nSEGMENTS:\n{json.dumps(ad_context, indent=2)}"
    ad_msgs = [{"role": "system", "content": AD_DETECT_PROMPT}, {"role": "user", "content": ad_prompt}]
    total_input_tokens += approx_tokens(AD_DETECT_PROMPT + ad_prompt)
    ad_resp = provider.chat_completion(messages=ad_msgs, temperature=0, response_format={"type": "json_object"})
    llm_calls += 1
    total_output_tokens += approx_tokens(ad_resp)

    try:
        ad_breaks = json.loads(extract_json(ad_resp))
        if isinstance(ad_breaks, dict):
            ad_breaks = ad_breaks.get("ads", ad_breaks.get("ad_breaks", []))
        if not isinstance(ad_breaks, list):
            ad_breaks = []
    except (json.JSONDecodeError, KeyError):
        ad_breaks = []

    ad_indices = set()
    for ad in ad_breaks:
        si, ei = ad.get("start_index", -1), ad.get("end_index", -1)
        sponsor = ad.get("sponsor", "Unknown")
        if 0 <= si < len(segments):
            ts_match = re.search(r"\[\d+:\d+(?::\d+)?\]", segments[si])
            ts = ts_match.group(0) if ts_match else "[??:??]"
            before_ctx = segments[si][:200]
            segments[si] = f"> **{ts} [AD BREAK]** - {sponsor}"
            for j in range(si + 1, min(ei + 1, len(segments))):
                ad_indices.add(j)
            total_corrections += 1
            all_applied_details.append(
                {
                    "type": "ad",
                    "find": before_ctx,
                    "replace": segments[si],
                    "before_context": before_ctx,
                    "after_context": segments[si],
                }
            )

    segments = [seg for i, seg in enumerate(segments) if i not in ad_indices]
    result_text = "\n\n".join(segments)
    elapsed = time.time() - start_time

    return CleaningResult(
        approach="C: Hybrid deterministic",
        episode_label=episode["label"],
        output_text=result_text,
        first_timestamp_sec=parse_first_timestamp(result_text),
        duration_sec=elapsed,
        llm_calls=llm_calls,
        input_tokens_approx=total_input_tokens,
        output_tokens_approx=total_output_tokens,
        content_retention=compute_content_retention(data["raw_data"].get("segments", []), result_text),
        corrections_count=total_corrections,
        corrections_detail=all_applied_details[:20],
    )


# ---------------------------------------------------------------------------
# APPROACH D: Current pipeline (full rewrite)
# ---------------------------------------------------------------------------
def approach_d_current_pipeline(data: Dict, episode: Dict, provider, formatter: TranscriptFormatter) -> CleaningResult:
    """Run the current TranscriptCleaner full-rewrite pipeline."""
    start_time = time.time()
    raw_data = data["raw_data"]
    podcast_facts = data["podcast_facts"]
    episode_facts = data["episode_facts"]

    formatted = formatter.format_transcript(raw_data)
    cleaner = TranscriptCleaner(provider)

    # Detect language from raw data
    language = raw_data.get("language", "en")
    if language and len(language) > 2:
        language = language[:2]

    # Track token usage via prompt callback
    token_info = {"input": 0, "output": 0, "calls": 0}

    def on_prompt_ready(info):
        token_info["input"] += info.get("input_chars", 0) // 4
        token_info["calls"] += 1

    cleaned = cleaner.clean_transcript(
        formatted_markdown=formatted,
        podcast_facts=podcast_facts,
        episode_facts=episode_facts,
        episode_title=episode_facts.episode_title,
        language=language,
        on_prompt_ready=on_prompt_ready,
    )

    elapsed = time.time() - start_time
    token_info["output"] = approx_tokens(cleaned)

    return CleaningResult(
        approach="D: Current pipeline",
        episode_label=episode["label"],
        output_text=cleaned,
        first_timestamp_sec=parse_first_timestamp(cleaned),
        duration_sec=elapsed,
        llm_calls=token_info["calls"],
        input_tokens_approx=token_info["input"],
        output_tokens_approx=token_info["output"],
        content_retention=compute_content_retention(data["raw_data"].get("segments", []), cleaned),
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_comparison(results: List[CleaningResult], raw_data: Dict):
    segments = raw_data.get("segments", [])
    raw_start = segments[0].get("start", 0) if segments else 0
    raw_end = segments[-1].get("end", 0) if segments else 0

    episode_label = results[0].episode_label
    print(f"\n{'='*110}")
    print(f"  {episode_label}")
    print(f"  Raw: {raw_end/60:.0f}min, starts at {raw_start:.1f}s, {len(segments)} segments")
    print(f"{'='*110}")

    header = f"{'Approach':<25} {'1st TS':>8} {'Retain%':>8} {'LLM#':>5} {'InTok':>8} {'OutTok':>8} {'TotalTok':>9} {'Time':>7} {'Fixes':>6}"
    print(header)
    print("-" * 110)

    for r in results:
        ts_str = (
            f"{int(r.first_timestamp_sec//60)}:{int(r.first_timestamp_sec%60):02d}"
            if r.first_timestamp_sec >= 0
            else "N/A"
        )
        total_tok = r.input_tokens_approx + r.output_tokens_approx
        print(
            f"{r.approach:<25} {ts_str:>8} {r.content_retention*100:>7.1f}% {r.llm_calls:>5} "
            f"{r.input_tokens_approx:>8} {r.output_tokens_approx:>8} {total_tok:>9} {r.duration_sec:>6.1f}s {r.corrections_count:>6}"
        )

    # Print correction examples with full sentence context
    for r in results:
        if not r.corrections_detail:
            continue
        print(f"\n  --- {r.approach}: Applied Corrections (up to 20) ---")
        for i, c in enumerate(r.corrections_detail[:20], 1):
            ctype = c.get("type", "?")
            reason = c.get("reason", "")
            before = c.get("before_context", "")
            after = c.get("after_context", "")
            # Truncate context lines sensibly
            if len(before) > 200:
                before = before[:200] + "..."
            if len(after) > 200:
                after = after[:200] + "..."
            print(f"  [{i}] type={ctype}" + (f"  reason: {reason}" if reason else ""))
            print(f"      BEFORE: {before}")
            print(f"      AFTER:  {after}")
            print()

    print()


def main():
    print("=" * 110)
    print("  TRANSCRIPT CLEANING PROTOTYPE COMPARISON  (B vs C vs D)")
    print("=" * 110)

    config = load_config()
    provider = create_llm_provider_from_config(config)
    print(f"\nUsing LLM provider: {provider.get_model_display_name()}")

    formatter = TranscriptFormatter()
    path_manager = PathManager("data")
    facts_manager = FactsManager(path_manager)

    all_results = []

    for episode in TEST_EPISODES:
        print(f"\n--- Loading: {episode['label']} ---")
        data = load_test_data(episode, facts_manager)
        if not data:
            continue

        episode_results = []

        # Existing cleaned text (reference)
        print("  [Ref] Evaluating existing cleaned text...")
        result_ref = evaluate_existing(data, episode)
        episode_results.append(result_ref)

        # Approach B: Corrections list
        print("  [B] Running corrections-list approach...")
        try:
            result_b = approach_b_corrections(data, episode, provider, formatter)
            episode_results.append(result_b)
        except Exception as e:
            print(f"    [ERROR] Approach B failed: {e}")

        # Approach C: Hybrid deterministic
        print("  [C] Running hybrid deterministic approach...")
        try:
            result_c = approach_c_hybrid(data, episode, provider, formatter)
            episode_results.append(result_c)
        except Exception as e:
            print(f"    [ERROR] Approach C failed: {e}")

        # Approach D: Current pipeline (full rewrite)
        print("  [D] Running current pipeline (full rewrite)...")
        try:
            result_d = approach_d_current_pipeline(data, episode, provider, formatter)
            episode_results.append(result_d)
        except Exception as e:
            print(f"    [ERROR] Approach D failed: {e}")

        print_comparison(episode_results, data["raw_data"])
        all_results.extend(episode_results)

    # Summary
    print("\n" + "=" * 110)
    print("  SUMMARY ACROSS ALL EPISODES")
    print("=" * 110)

    approaches = {}
    for r in all_results:
        approaches.setdefault(r.approach, []).append(r)

    header = f"{'Approach':<25} {'Avg Retain%':>12} {'TS OK':>8} {'Avg LLM#':>9} {'Avg InTok':>10} {'Avg OutTok':>11} {'Avg Total':>10} {'Avg Time':>9}"
    print(header)
    print("-" * 100)

    for name, results in approaches.items():
        n = len(results)
        avg_ret = sum(r.content_retention for r in results) / n * 100
        ts_ok = sum(1 for r in results if r.first_timestamp_sec <= 10)
        avg_llm = sum(r.llm_calls for r in results) / n
        avg_in = sum(r.input_tokens_approx for r in results) / n
        avg_out = sum(r.output_tokens_approx for r in results) / n
        avg_total = avg_in + avg_out
        avg_time = sum(r.duration_sec for r in results) / n
        print(
            f"{name:<25} {avg_ret:>11.1f}% {ts_ok:>4}/{n:<3} {avg_llm:>8.1f} {avg_in:>10.0f} {avg_out:>10.0f} {avg_total:>10.0f} {avg_time:>8.1f}s"
        )

    # Save outputs
    output_dir = Path("data/cleaning_prototype_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    for r in all_results:
        safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", r.episode_label)
        safe_approach = re.sub(r"[^a-zA-Z0-9_-]", "_", r.approach)
        (output_dir / f"{safe_label}__{safe_approach}.md").write_text(r.output_text)

    print(f"\nOutputs saved to {output_dir}/")


if __name__ == "__main__":
    main()
