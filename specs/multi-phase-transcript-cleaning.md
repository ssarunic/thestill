# Multi-Phase Transcript Cleaning Architecture

**Status:** Proposal
**Created:** 2026-01-13
**Context:** LLM hallucination issues during transcript cleaning - model skips content and synthesizes instead of editing verbatim.

---

## Problem Statement

Current single-pass cleaning approach has critical issues:

1. LLM skips large portions of transcript (e.g., first 47 minutes)
2. Model synthesizes/summarizes instead of cleaning verbatim
3. Cold opens (teaser clips) confuse the model
4. No way to audit or fix specific aspects without full reprocess

---

## 1. Multi-Phase Cleaning: Token Cost Problem

**The Problem:**
If you have 100K tokens of transcript and 5 phases, naive approach = 500K tokens input.

**Clever Solutions:**

### A. Hierarchical Processing (Different granularities per phase)

| Phase | Operates On | Token Cost |
|-------|-------------|------------|
| Speaker ID | Segment metadata only (timestamps + first 10 words) | ~5% of full |
| Ad Detection | Segment metadata + keyword scan | ~10% of full |
| Cold Open Detection | First 5 minutes only | ~5% of full |
| Spelling/Grammar | Full text, but chunked | 100% |
| Final Assembly | Deterministic merge | 0 (no LLM) |

Total: ~120% instead of 500%

### B. Prompt Caching (Claude/Anthropic specific)

Claude's prompt caching works like this:

```
[CACHED: System prompt + podcast facts + episode facts] → pay once, reuse
[VARIABLE: Transcript chunk] → pay per chunk
```

For multi-phase on same transcript:

- Phase 1: Cache miss on system prompt (~2K tokens) + transcript (100K)
- Phase 2: Cache HIT on transcript, new system prompt (~2K)
- Phase 3: Cache HIT on transcript, new system prompt (~2K)

Savings: ~60-80% on phases 2+ if transcript is cached.

However, caching requires:

- Same `cache_control` markers
- Requests within cache TTL (currently 5 minutes for Anthropic)
- Minimum 1024 tokens for cacheable block

### C. Structured Delta Approach

Instead of re-processing full text, each phase outputs **only changes**:

```json
// Phase 1 output (Speaker ID)
{
  "segment_updates": [
    {"id": 0, "speaker": "Lenny Rachitsky"},
    {"id": 1, "speaker": "Molly Graham"}
  ]
}

// Phase 2 output (Ad Detection)
{
  "segment_updates": [
    {"id": 45, "type": "ad", "sponsor": "DX"},
    {"id": 46, "type": "ad", "sponsor": "DX"}
  ]
}
```

**Final assembly is deterministic (no LLM).**

---

## 2. Keeping Structured JSON vs Markdown

### Current Flow

```
Raw JSON → TranscriptFormatter → Markdown → LLM → Cleaned Markdown
```

### Proposed Structured Flow

```
Raw JSON → Annotated JSON → LLM patches → Merged JSON → Final Markdown
```

### Pros of Structured Approach

| Benefit | Why It Matters |
|---------|----------------|
| **Precise operations** | "Change segment 47's speaker" vs "find and replace in blob" |
| **Auditable changes** | Git-diff friendly, can see exactly what LLM changed |
| **Partial reprocessing** | Re-run only speaker ID without touching spelling fixes |
| **Validation** | Assert constraints (timestamps monotonic, no missing segments) |
| **Rollback** | Revert specific phases without full reprocess |
| **Parallelization** | Process segments 1-50 and 51-100 in parallel |

### Cons of Structured Approach

| Drawback | Mitigation |
|----------|------------|
| **Context fragmentation** | Include N surrounding segments for context |
| **Cross-segment dependencies** | "This speaker continued from previous" needs context window |
| **More complex code** | Worth it for reliability |
| **LLMs prefer prose** | Use prose for analysis, structured for output |

### Proposed Data Model

```python
@dataclass
class AnnotatedSegment:
    """Single segment with all annotations."""
    id: int
    start: float
    end: float
    text: str
    raw_speaker: str  # Original: SPEAKER_00

    # Phase annotations (added incrementally)
    identified_speaker: Optional[str] = None  # Phase 1
    segment_type: str = "content"  # Phase 2: "content", "ad", "cold_open", "credits"
    ad_sponsor: Optional[str] = None
    cleaned_text: Optional[str] = None  # Phase 3
    spelling_corrections: List[dict] = field(default_factory=list)

    # Metadata
    confidence: float = 1.0
    needs_review: bool = False
    review_reason: Optional[str] = None

@dataclass
class AnnotatedTranscript:
    """Full transcript with segment-level annotations."""
    episode_id: str
    segments: List[AnnotatedSegment]

    # Episode-level annotations
    cold_open_end_segment: Optional[int] = None
    main_content_start_segment: Optional[int] = None
    detected_speakers: Dict[str, str] = field(default_factory=dict)
    detected_ads: List[dict] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render final cleaned markdown."""
        ...

    def apply_patch(self, patch: dict) -> None:
        """Apply LLM-generated patch to segments."""
        ...
```

### Phase Processing with Context Windows

```python
def process_segment_batch(
    segments: List[AnnotatedSegment],
    context_before: List[AnnotatedSegment],  # 3-5 segments for context
    context_after: List[AnnotatedSegment],
    phase: str
) -> List[SegmentPatch]:
    """
    Process a batch of segments with surrounding context.
    LLM sees context but only outputs patches for target segments.
    """
    prompt = f"""
    CONTEXT (do not modify, for reference only):
    {format_segments(context_before)}

    SEGMENTS TO PROCESS (output patches for these):
    {format_segments(segments)}

    CONTEXT (do not modify, for reference only):
    {format_segments(context_after)}

    Output JSON patches for segments {segments[0].id} to {segments[-1].id} only.
    """
```

---

## 3. Multi-Phase Summarization

### Current Problem

Single massive prompt → model loses focus, inconsistent sections.

### Proposed Approach

```
Phase 1: Extract raw material (quotes, facts, claims) → structured JSON
Phase 2: Generate each section independently using extracted material
Phase 3: Assemble + ensure consistency
```

### Smart Caching for Summarization

```python
# Phase 1: Full transcript read (expensive, but cached)
extracted = extract_material(transcript)  # 100K tokens in

# Phase 2: Section generation (uses extracted material, not full transcript)
sections = {}
for section in ["gist", "drama", "quotes", "angles"]:
    # Each call: ~5K tokens (extracted material) + ~1K (section prompt)
    # With caching: extracted material cached after first section
    sections[section] = generate_section(
        extracted_material=extracted,  # CACHED after first call
        section_type=section,
        section_prompt=PROMPTS[section]
    )
```

**Cost comparison:**

- Current: 100K tokens × 1 call = 100K
- Multi-phase naive: 100K × 5 sections = 500K
- Multi-phase with extraction: 100K + (5K × 5) = 125K
- Multi-phase with caching: 100K + 5K + (1K × 4) = 109K

---

## 4. Quality Loss from Chunking?

**The Real Risk:**
Model doesn't see that segment 150 references something from segment 20.

**Mitigations:**

### A. Two-Pass Architecture

```
Pass 1 (Full Context): Read entire transcript, extract:
  - Speaker mapping
  - Topic structure
  - Cross-references ("as I mentioned earlier...")
  - Episode arc

Pass 2 (Chunked): Process chunks with Pass 1 metadata as context
```

### B. Overlap Windows

```
Chunk 1: Segments 1-50 + peek at 51-55
Chunk 2: Segments 46-100 + peek at 101-105
Chunk 3: Segments 96-150 + peek at 151-155

→ Merge with preference for middle of each chunk
```

### C. Hierarchical Summary

```
Chunk summaries → Episode summary → Use episode summary as context for all chunks
```

---

## 5. Recommended Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    RAW TRANSCRIPT (JSON)                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 0: STRUCTURE ANALYSIS (full transcript, read-only)       │
│  - Detect cold open boundaries                                  │
│  - Identify episode arc/structure                               │
│  - Extract speaker voice patterns                               │
│  Output: EpisodeStructure metadata                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 1: SPEAKER IDENTIFICATION (segment metadata only)        │
│  Input: First 20 words per segment + EpisodeStructure           │
│  Output: SpeakerMapping patches                                 │
│  Cost: ~10% of full transcript                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 2: SEGMENT CLASSIFICATION (deterministic + light LLM)    │
│  - Ad detection (keyword scan + LLM verify)                     │
│  - Cold open tagging (use Phase 0 boundaries)                   │
│  - Credits detection                                            │
│  Output: SegmentType patches                                    │
│  Cost: ~15% of full transcript                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 3: TEXT CLEANING (chunked, with context windows)         │
│  - Spelling/grammar fixes                                       │
│  - Proper noun correction                                       │
│  - Filler word removal                                          │
│  Input: Chunks of ~20K chars + 5 segment context each side      │
│  Output: CleanedText patches                                    │
│  Cost: ~120% of full transcript (overlap)                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 4: ASSEMBLY (deterministic, no LLM)                      │
│  - Apply all patches to AnnotatedTranscript                     │
│  - Validate constraints                                         │
│  - Render to Markdown                                           │
│  Cost: 0 LLM tokens                                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CLEANED TRANSCRIPT                           │
│              (Markdown + Annotated JSON sidecar)                │
└─────────────────────────────────────────────────────────────────┘
```

**Estimated Total Cost: ~145% of current single-pass**
**But: Much more reliable, auditable, and fixable**

---

## 6. Quick Wins to Try First

Before full architecture overhaul:

1. **Reduce chunk size** from 52K to 25K chars - may fix hallucination issue immediately
2. **Add validation** - compare input/output segment counts, flag if >10% difference
3. **Try different model** - Claude Sonnet or GPT-4 may be more faithful than Gemini Flash
4. **Add word count check** - reject output if word count differs >20% from input

---

## 7. Implementation Phases

### Phase A: Quick Fixes (1-2 days)

- Reduce chunk size
- Add validation checks
- Test with different models

### Phase B: Structured Data Model (3-5 days)

- Implement AnnotatedSegment/AnnotatedTranscript
- Refactor TranscriptFormatter to produce structured output
- Keep Markdown rendering as final step

### Phase C: Multi-Phase Pipeline (1-2 weeks)

- Implement Phase 0 (Structure Analysis)
- Implement Phase 1 (Speaker ID) with reduced token input
- Implement Phase 2 (Classification)
- Refactor Phase 3 (Text Cleaning) to use patches
- Implement deterministic assembly

### Phase D: Summarization Refactor (1 week)

- Implement material extraction phase
- Split section generation
- Add prompt caching

---

## 8. Open Questions

1. Should we keep both JSON and Markdown outputs, or derive Markdown from JSON only?
2. What's the right context window size for chunked processing?
3. How do we handle episodes where cold open detection fails?
4. Should we add human-in-the-loop review for low-confidence segments?
5. How do we measure quality improvement objectively?

---

## References

- Current implementation: `thestill/core/transcript_cleaner.py`
- Facts extraction: `thestill/core/facts_extractor.py`
- Transcript formatter: `thestill/core/transcript_formatter.py`
- Episode that triggered this investigation: "The high-growth handbook: Molly Graham's frameworks" (Lenny's Podcast)
