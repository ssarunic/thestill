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

"""Segment-by-segment LLM cleanup with neighbour context (spec #18 Phase C).

The segmented cleaner is the LLM-side companion to
:class:`~thestill.core.transcript_segmenter.TranscriptSegmenter`. It
consumes a deterministic ``AnnotatedTranscript`` produced by the
segmenter and returns a cleaned ``AnnotatedTranscript`` whose segment
grid is unchanged but whose text has been corrected, whose filler has
been flagged, and whose ad spans have been tagged with sponsor names.

The LLM sees each batch as a JSON document with three sections —
``k_prev`` already-cleaned preceding segments, the target batch, and
``k_next`` upcoming raw segments — and responds with a
:class:`CleanupPatchBatch`. The patch schema deliberately omits
``source_segment_ids`` and ``source_word_span`` so the LLM cannot
rewrite them — the "patch must not touch source anchors" invariant
(spec #18 §"Future: user segment-editing UI — identity scheme") is
enforced by construction via Pydantic validation.

Prompt caching is engaged via
:meth:`LLMProvider.generate_structured_cached`. The cacheable prefix
(system prompt + facts + speaker mapping + sponsors) is identical
across every call for an episode; providers that honour the hint
(Anthropic) mark the system block ephemerally cacheable, providers
that auto-cache (OpenAI / Gemini) do so transparently, and
providers without caching (Ollama / Mistral) have their batch budget
widened to amortise the repeated prefix.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from structlog import get_logger

from thestill.core.llm_provider import LLMProvider
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript, SegmentKind
from thestill.models.facts import EpisodeFacts, PodcastFacts, strip_role_annotation
from thestill.utils.language_config import resolve_language_spec

logger = get_logger(__name__)


class CleanupPatch(BaseModel):
    """One LLM-produced patch for a target segment.

    The schema deliberately omits ``source_segment_ids`` and
    ``source_word_span`` — patches may not touch them. Pydantic's
    structured-output validation drops any extra fields the LLM
    hallucinates before they reach :meth:`SegmentedTranscriptCleaner._apply_patches`,
    so the source-anchor invariant is enforced at the schema boundary
    rather than by defensive code.
    """

    id: int = Field(..., description="Positional id of the target segment being patched.")
    cleaned_text: str = Field(
        ...,
        description="Corrected text for the segment. Empty only for 'filler'. "
        "For 'ad_break', 'music', 'intro', 'outro' keep the full cleaned text — "
        "downstream consumers filter by kind, they do not rely on the LLM to "
        "redact.",
    )
    kind: Literal["content", "filler", "ad_break", "music", "intro", "outro"] = Field(
        default="content",
        description="Segment tag. 'content' is the default narrative bucket. "
        "'filler' is um/uh/you-know-style noise and is dropped from rendered "
        "output. 'ad_break' tags sponsor reads; 'music' tags theme or "
        "interstitial music; 'intro' and 'outro' tag pre-/post-roll segments "
        "that are not the main discussion. Rendering policy (keep / drop / "
        "annotate) lives at the consumer layer — tag, do not redact.",
    )
    sponsor: Optional[str] = Field(
        default=None,
        description="Sponsor name when kind='ad_break'. Populated from the facts "
        "list when possible, otherwise best-effort.",
    )


class CleanupPatchBatch(BaseModel):
    """Response shape for one batch of target segments.

    The LLM returns exactly one entry per target segment (missing entries
    fall back to the pre-patch segment). Entries for segment ids outside
    the target range (e.g. hallucinated from the context) are silently
    ignored by :meth:`SegmentedTranscriptCleaner._apply_patches`.
    """

    patches: List[CleanupPatch] = Field(
        default_factory=list,
        description="One patch per target segment. Order is not significant.",
    )


class SegmentedTranscriptCleaner:
    """Segment-by-segment LLM cleaner with neighbour context and prompt caching."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        k_prev: int = 2,
        k_next: int = 2,
        batch_char_budget: int = 4000,
        temperature: float = 0.0,
    ) -> None:
        """
        Args:
            provider: The LLM provider used for each per-batch call.
            k_prev: Number of preceding already-cleaned segments included
                as context. Helps the LLM maintain tone/speaker continuity
                across batches.
            k_next: Number of upcoming raw segments included as read-only
                forward context. Helps the LLM anticipate sentence
                completions and ad-break boundaries.
            batch_char_budget: Target upper bound on the combined character
                count of segments the LLM cleans in one call. Widened 3x
                automatically for providers without prompt caching, to
                amortise the repeated prefix across fewer, bigger calls.
            temperature: Sampling temperature. Default 0 for deterministic
                output; evaluators can bump this for A/B measurements.

        Raises:
            ValueError: When any argument is outside its valid range.
        """
        if k_prev < 0:
            raise ValueError(f"k_prev must be >= 0, got {k_prev}")
        if k_next < 0:
            raise ValueError(f"k_next must be >= 0, got {k_next}")
        if batch_char_budget < 1:
            raise ValueError(f"batch_char_budget must be >= 1, got {batch_char_budget}")

        self.provider = provider
        self.k_prev = k_prev
        self.k_next = k_next
        self.temperature = temperature

        # Widen the batch budget for providers without caching so the
        # repeated prefix gets amortised across fewer calls. The 3x
        # multiplier is a starting heuristic; spec #18 Open Question #1
        # will tune it against the eval harness.
        self._effective_batch_char_budget = (
            batch_char_budget if provider.supports_prompt_caching() else batch_char_budget * 3
        )

    def clean(
        self,
        annotated: AnnotatedTranscript,
        podcast_facts: Optional[PodcastFacts],
        episode_facts: EpisodeFacts,
        *,
        language: str,
    ) -> AnnotatedTranscript:
        """Return a cleaned copy of ``annotated``.

        The returned transcript carries the same ``episode_id``,
        ``playback_time_offset_seconds``, and ``algorithm_version`` as
        the input. Segment ids are reassigned positionally after all
        patches apply. ``source_segment_ids`` and ``source_word_span``
        are preserved unchanged — the patch schema forbids the LLM from
        touching them.
        """
        source = _apply_speaker_mapping(annotated.segments, episode_facts.speaker_mapping)

        system_prompt = self._build_system_prompt(
            language=language,
            podcast_facts=podcast_facts,
            episode_facts=episode_facts,
        )

        cleaned: List[AnnotatedSegment] = []
        total = len(source)
        index = 0
        while index < total:
            batch_end = self._pick_batch_end(source, start=index)
            target = source[index:batch_end]
            prev_context = cleaned[-self.k_prev :] if self.k_prev else []
            next_context = source[batch_end : batch_end + self.k_next]

            user_prompt = self._build_user_prompt(
                prev_context=prev_context,
                target=target,
                next_context=next_context,
            )

            logger.debug(
                "segmented_cleanup_batch",
                episode_id=annotated.episode_id,
                start=index,
                end=batch_end,
                prev_context=len(prev_context),
                next_context=len(next_context),
                target_chars=sum(len(s.text) for s in target),
            )

            patch_batch = self.provider.generate_structured_cached(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_model=CleanupPatchBatch,
                cache_system_message=True,
                temperature=self.temperature,
            )

            patched = self._apply_patches(target, patch_batch.patches)
            cleaned.extend(patched)
            index = batch_end

        # Reassign positional ids so the returned transcript remains
        # 0..N-1 after any filler-drop / ad-merge surgery that Phase D's
        # renderers may perform later. This is the invariant the frontend
        # relies on for stable React keys.
        for new_id, segment in enumerate(cleaned):
            segment.id = new_id

        return AnnotatedTranscript(
            episode_id=annotated.episode_id,
            segments=cleaned,
            playback_time_offset_seconds=annotated.playback_time_offset_seconds,
            algorithm_version=annotated.algorithm_version,
        )

    # ------------------------------------------------------------------
    # Internal helpers — split out for testability and to keep ``clean``
    # focused on orchestration.
    # ------------------------------------------------------------------

    def _pick_batch_end(self, segments: List[AnnotatedSegment], *, start: int) -> int:
        """Return the exclusive end index for the batch starting at ``start``.

        Always consumes at least one segment so the loop makes progress,
        even when a single segment's text exceeds the budget. Subsequent
        segments are added greedily until the cumulative character count
        would exceed the effective budget.
        """
        end = start + 1
        accumulated = len(segments[start].text)
        total = len(segments)
        while end < total:
            next_length = len(segments[end].text)
            if accumulated + next_length > self._effective_batch_char_budget:
                break
            accumulated += next_length
            end += 1
        return end

    def _apply_patches(
        self,
        target: List[AnnotatedSegment],
        patches: List[CleanupPatch],
    ) -> List[AnnotatedSegment]:
        """Apply ``patches`` to ``target`` in-place-safe fashion.

        Invariants enforced here:

        - ``source_segment_ids`` and ``source_word_span`` are preserved
          untouched. The :class:`CleanupPatch` schema declares neither
          field, so the LLM physically cannot request changes to them.
          This method relies on that contract and does not re-assign
          them.
        - Patches keyed to segment ids outside the target batch (e.g.
          hallucinations referencing neighbour-context ids) are silently
          dropped with a debug log.
        - Target segments with no corresponding patch pass through
          unchanged and emit a debug log so missing-patch symptoms are
          visible when tracing a single batch.
        """
        by_id: Dict[int, CleanupPatch] = {p.id: p for p in patches}
        out: List[AnnotatedSegment] = []
        target_ids = {seg.id for seg in target}

        for seg in target:
            patch = by_id.get(seg.id)
            if patch is None:
                logger.debug("segmented_cleanup_missing_patch", segment_id=seg.id)
                out.append(seg)
                continue

            kind: SegmentKind = patch.kind
            updated = seg.model_copy(
                update={
                    "text": patch.cleaned_text,
                    "kind": kind,
                    "sponsor": patch.sponsor,
                }
            )
            out.append(updated)

        # Log any patches the LLM produced that weren't claimed by a
        # target segment. Non-fatal — just useful when trimming prompts.
        stray_ids = [p.id for p in patches if p.id not in target_ids]
        if stray_ids:
            logger.debug(
                "segmented_cleanup_stray_patches",
                stray_ids=stray_ids,
                target_ids=sorted(target_ids),
            )

        return out

    def _build_system_prompt(
        self,
        *,
        language: str,
        podcast_facts: Optional[PodcastFacts],
        episode_facts: EpisodeFacts,
    ) -> str:
        """Build the cacheable system prefix.

        Identical across every batch call for the episode — this is what
        makes prompt caching worthwhile. Contains the language directive,
        behaviour rules, and the full facts context.
        """
        lang_config = resolve_language_spec(language)
        lang_name = lang_config["name"]
        spelling_rules = lang_config["spelling"]

        facts_block = self._render_facts_block(podcast_facts, episode_facts)

        return (
            "You are an expert podcast transcript editor. You receive batches "
            "of diarised transcript segments as structured JSON and return "
            "patches that correct spelling, remove filler, and tag ads. You "
            "NEVER paraphrase or synthesise — your output stays 95%+ identical "
            f"to the input text, with only minor corrections.\n\n"
            f"LANGUAGE:\nThe transcript is in {lang_name}. Apply {spelling_rules}. "
            f"Keep all cleaned_text in {lang_name}.\n\n"
            "INPUT SHAPE:\n"
            "You will receive a JSON object with three keys:\n"
            "- 'previous_cleaned': already-cleaned segments for tone and "
            "  speaker continuity. Do NOT output patches for these.\n"
            "- 'target': the segments you must patch. Output one patch per "
            "  target segment.\n"
            "- 'next_raw': upcoming raw segments for forward context. Do NOT "
            "  output patches for these.\n\n"
            "OUTPUT SHAPE:\n"
            "Return a JSON object with a 'patches' array. Each patch has:\n"
            "- 'id': integer, copied from the target segment's id field.\n"
            "- 'cleaned_text': string, the corrected text. Empty string only "
            "  for filler-only segments.\n"
            "- 'kind': 'content' (default), 'filler', 'ad_break', 'music', "
            "  'intro', or 'outro'.\n"
            "- 'sponsor': string, the sponsor name when kind='ad_break'; "
            "  null otherwise.\n\n"
            "CLEANUP RULES:\n"
            "1. VERBATIM PRESERVATION — fix only obvious transcription errors "
            "   (homophones, misheard proper nouns, garbled words). Do NOT "
            "   restructure sentences. Do NOT improve eloquence.\n"
            f"2. SPELLING — apply {spelling_rules} uniformly.\n"
            "3. FILLER — segments that are almost entirely 'um', 'uh', "
            "   'like', 'you know' become kind='filler' with empty "
            "   cleaned_text. Isolated filler words within a content segment "
            "   can be dropped but the segment stays kind='content'.\n"
            "4. AD DETECTION — when a segment is clearly a sponsor read "
            "   ('Support for the show comes from', 'promo code', "
            "   'visit [sponsor].com'), mark it kind='ad_break' and populate "
            "   sponsor from the known sponsors list when possible. KEEP the "
            "   full cleaned ad text in cleaned_text — downstream consumers "
            "   filter by kind, they do not rely on you to redact. Tagging, "
            "   not obfuscation, is the contract.\n"
            "5. SEGMENT TAGS — also use kind='music' for theme/interstitial "
            "   music spans that the transcriber still produced text for, "
            "   kind='intro' for pre-roll show openings (cold opens, "
            "   credits, 'welcome to the show'), and kind='outro' for "
            "   post-roll sign-offs and plugs. Keep cleaned_text populated "
            "   in every case — the UI toggles visibility per kind.\n"
            "6. ENTITY REPAIR — fix proper nouns using the Keywords list. "
            "   Names mangled at episode end often map to the Production "
            "   Team list. Flag uncertain names with '[?]'.\n"
            "7. NO HALLUCINATION — if you cannot correct a word, keep it as-is.\n\n"
            f"FACTS CONTEXT:\n{facts_block}"
        )

    def _render_facts_block(
        self,
        podcast_facts: Optional[PodcastFacts],
        episode_facts: EpisodeFacts,
    ) -> str:
        """Flatten the facts models into a compact prompt-friendly block."""
        lines: List[str] = []
        if podcast_facts:
            lines.append("PODCAST:")
            if podcast_facts.hosts:
                lines.append(f"  Hosts: {', '.join(podcast_facts.hosts)}")
            if podcast_facts.production_team:
                lines.append(f"  Production team: {', '.join(podcast_facts.production_team)}")
            if podcast_facts.recurring_roles:
                lines.append(f"  Recurring roles: {', '.join(podcast_facts.recurring_roles)}")
            if podcast_facts.sponsors:
                lines.append(f"  Known sponsors: {', '.join(podcast_facts.sponsors)}")
            if podcast_facts.keywords:
                lines.append(f"  Keywords/mishearings: {', '.join(podcast_facts.keywords)}")
            if podcast_facts.style_notes:
                lines.append(f"  Style: {', '.join(podcast_facts.style_notes)}")

        lines.append("EPISODE:")
        lines.append(f"  Title: {episode_facts.episode_title}")
        if episode_facts.speaker_mapping:
            mapping = ", ".join(f"{speaker_id}={name}" for speaker_id, name in episode_facts.speaker_mapping.items())
            lines.append(f"  Speaker mapping: {mapping}")
        if episode_facts.guests:
            lines.append(f"  Guests: {', '.join(episode_facts.guests)}")
        if episode_facts.topics_keywords:
            lines.append(f"  Topics: {', '.join(episode_facts.topics_keywords)}")
        if episode_facts.ad_sponsors:
            lines.append(f"  Ad sponsors this episode: {', '.join(episode_facts.ad_sponsors)}")

        return "\n".join(lines)

    def _build_user_prompt(
        self,
        *,
        prev_context: List[AnnotatedSegment],
        target: List[AnnotatedSegment],
        next_context: List[AnnotatedSegment],
    ) -> str:
        """Serialise the three buckets as a compact JSON payload.

        ``previous_cleaned`` and ``next_raw`` are context only; the LLM
        must not produce patches for their ids. ``target`` is the set to
        patch.
        """
        import json as _json

        payload = {
            "previous_cleaned": [_segment_to_prompt_dict(s) for s in prev_context],
            "target": [_segment_to_prompt_dict(s) for s in target],
            "next_raw": [_segment_to_prompt_dict(s) for s in next_context],
        }
        return _json.dumps(payload, ensure_ascii=False)


def _segment_to_prompt_dict(segment: AnnotatedSegment) -> Dict[str, object]:
    """Render a segment as a compact JSON-serialisable dict for the prompt.

    Source anchors (``source_segment_ids``, ``source_word_span``) are
    intentionally omitted — they are irrelevant to the LLM's task and
    would only invite patches attempting to modify them.
    """
    return {
        "id": segment.id,
        "start": round(segment.start, 2),
        "end": round(segment.end, 2),
        "speaker": segment.speaker,
        "text": segment.text,
        "kind": segment.kind,
    }


def _apply_speaker_mapping(
    segments: List[AnnotatedSegment],
    mapping: Dict[str, str],
) -> List[AnnotatedSegment]:
    """Return a new segment list with speaker ids substituted by real names.

    Mirrors the deterministic substitution the legacy
    :meth:`thestill.core.transcript_cleaner.TranscriptCleaner._apply_speaker_mapping`
    did as Stage 2a, but operates on the structured ``speaker`` field of
    each :class:`AnnotatedSegment` rather than doing a regex over
    Markdown text.

    - An empty or ``None`` ``mapping`` passes through unchanged.
    - Entries with an empty name in the mapping are skipped (ids left
      unchanged) — same semantics as the legacy stage.
    - Trailing ``" (Role)"`` annotations are stripped so
      ``"Scott Galloway (Host)"`` renders as ``"Scott Galloway"``.
      Only the *last* parenthesised block is stripped, so a name like
      ``"A (B) (Host)"`` becomes ``"A (B)"``.
    - Unmapped speakers (and ``speaker=None`` segments) pass through
      unchanged. ``SPEAKER_NN`` labels that survive to output are a
      visible canary: facts extraction missed that speaker and the
      facts file needs editing + a re-clean.
    """
    if not mapping:
        return segments

    normalised: Dict[str, str] = {}
    for speaker_id, name in mapping.items():
        if not name:
            continue
        clean = strip_role_annotation(name).strip()
        if clean:
            normalised[speaker_id] = clean

    if not normalised:
        return segments

    return [
        (
            segment.model_copy(update={"speaker": normalised[segment.speaker]})
            if segment.speaker is not None and segment.speaker in normalised
            else segment
        )
        for segment in segments
    ]
