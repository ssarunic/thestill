"""Diagnose why Gemini returns empty response when cleaning epizoda-218 (F/M/K).

Monkey-patches ``GeminiProvider.generate_structured`` to dump full response
metadata — finish_reason, safety_ratings, prompt_feedback, candidate count,
usage_metadata — instead of raising a bare "empty response" error. Then runs
the segmented cleaner against the existing raw transcript and pre-saved
episode facts for episode 53712f4c.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from thestill.core.llm_provider import GeminiProvider, create_llm_provider_from_config
from thestill.utils.config import load_config
from thestill.utils.path_manager import PathManager

EPISODE_ID = "53712f4c-0649-4970-a76f-fc53cee645b0"
TRANSCRIPT_PATH = REPO / "data/raw_transcripts/mjesto-zlocina/epizoda-218-f-m-k_transcript.json"
PODCAST_SLUG = "mjesto-zlocina"
EPISODE_SLUG = "epizoda-218-f-m-k"
PODCAST_TITLE = "Mjesto Zločina"
PODCAST_DESCRIPTION = "Prvi hrvatski True Crime podcast."
EPISODE_TITLE = "Epizoda 218: F/M/K"
EPISODE_DESCRIPTION = (
    '"Osa nos ti posra" - ako slušate isključivo zbog true crimea preskočite '
    "ovu epizodu, ako slušate zbog nas - uživajte."
)


def short(obj, limit: int = 800) -> str:
    s = repr(obj)
    return s if len(s) <= limit else s[:limit] + f"... <truncated {len(s) - limit} chars>"


def install_diag(provider: GeminiProvider) -> None:
    original = provider.generate_structured
    call_count = {"n": 0}

    def wrapped(messages, response_model, temperature=None, max_tokens=None):
        call_count["n"] += 1
        idx = call_count["n"]
        # Reproduce the call path used inside generate_structured so we can
        # inspect the raw response BEFORE the "empty response" check fires.
        contents = provider._convert_messages(messages)
        config = provider._build_config(
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_model,
        )
        print(f"\n=== Gemini call #{idx} ===")
        print(f"model: {provider.model}")
        print(
            f"messages: roles={[m.get('role') for m in messages]} "
            f"sys_chars={sum(len(m['content']) for m in messages if m.get('role') == 'system')} "
            f"user_chars={sum(len(m['content']) for m in messages if m.get('role') == 'user')}"
        )
        try:
            response = provider.client.models.generate_content(
                model=provider.model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            print(f"  API error: {type(e).__name__}: {e}")
            raise

        # Dump everything we can inspect on the response.
        print(f"  prompt_feedback: {short(getattr(response, 'prompt_feedback', None))}")
        print(f"  usage_metadata:  {short(getattr(response, 'usage_metadata', None))}")
        candidates = response.candidates or []
        print(f"  candidate_count: {len(candidates)}")
        for i, cand in enumerate(candidates):
            print(f"  candidate[{i}].finish_reason: {cand.finish_reason}")
            print(f"  candidate[{i}].safety_ratings: {short(getattr(cand, 'safety_ratings', None))}")
            content = getattr(cand, "content", None)
            if content is None:
                print(f"  candidate[{i}].content: None")
                continue
            parts = getattr(content, "parts", None) or []
            print(f"  candidate[{i}].content.role: {getattr(content, 'role', None)}")
            print(f"  candidate[{i}].content.parts: count={len(parts)}")
            for j, p in enumerate(parts):
                text = getattr(p, "text", None)
                thought = getattr(p, "thought", None)
                print(
                    f"    part[{j}]: thought={thought!r} "
                    f"text_len={len(text) if text else 0} "
                    f"text_preview={short(text, 200) if text else None}"
                )

        # Try the same extraction logic as the real method.
        response_text = None
        try:
            response_text = response.text
        except Exception as e:
            print(f"  response.text raised: {type(e).__name__}: {e}")
            if candidates and candidates[0].content and candidates[0].content.parts:
                try:
                    response_text = candidates[0].content.parts[0].text
                except Exception as e2:
                    print(f"  fallback parts[0].text raised: {type(e2).__name__}: {e2}")
        print(f"  extracted text_len: {len(response_text) if response_text else 0}")

        if not response_text:
            raise ValueError("Gemini returned empty response (diagnostic mode)")
        data = json.loads(response_text)
        return response_model(**data)

    provider.generate_structured = wrapped  # type: ignore[assignment]


def main() -> int:
    config = load_config()
    print(f"LLM provider: {config.llm_provider}")
    print(f"Gemini model: {config.gemini_model}")
    print(f"Gemini thinking_level: {getattr(config, 'gemini_thinking_level', None)}")

    provider = create_llm_provider_from_config(config)
    if not isinstance(provider, GeminiProvider):
        print(f"Expected GeminiProvider, got {type(provider).__name__} — aborting.")
        return 1

    install_diag(provider)

    with open(TRANSCRIPT_PATH) as f:
        transcript_data = json.load(f)
    print(f"Loaded transcript: {len(transcript_data.get('segments', []))} segments")

    path_manager = PathManager()

    # Lazy import to avoid heavy module-load when only inspecting failure mode.
    from thestill.core.transcript_cleaning_processor import TranscriptCleaningProcessor

    processor = TranscriptCleaningProcessor(provider=provider)

    try:
        result = processor.clean_transcript(
            transcript_data=transcript_data,
            podcast_title=PODCAST_TITLE,
            podcast_description=PODCAST_DESCRIPTION,
            episode_title=EPISODE_TITLE,
            episode_description=EPISODE_DESCRIPTION,
            podcast_slug=PODCAST_SLUG,
            episode_slug=EPISODE_SLUG,
            path_manager=path_manager,
            save_prompts=False,
            language="hr",
        )
        print(
            f"\nUnexpectedly succeeded. result keys: {list(result.keys()) if isinstance(result, dict) else type(result)}"
        )
        return 0
    except Exception as e:
        print(f"\nClean failed (expected): {type(e).__name__}: {e}")
        print("\n--- traceback ---")
        traceback.print_exc()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
