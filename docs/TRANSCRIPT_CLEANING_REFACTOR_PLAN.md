# Transcript Cleaning Processor Refactoring Plan

**Created:** 2025-11-26
**Status:** Planned
**Author:** Staff Engineer Review + Claude Analysis

## Background

Following a staff engineer review of `thestill/core/transcript_cleaning_processor.py`, several improvements were identified to enhance reliability, observability, and correctness of the transcript cleaning pipeline.

## Current Architecture

The pipeline has 4 phases:
- **Phase 0:** Format JSON → Markdown (deterministic)
- **Phase 1:** LLM analyzes transcript, returns corrections list
- **Phase 1.5:** Apply corrections deterministically
- **Phase 2:** LLM identifies speakers from sample chunks
- **Phase 3:** Apply speaker mapping deterministically

## Identified Issues

### Critical Issues

1. **Silent Failures in Phase 1 (Lines 392-450)**
   - JSON parsing errors are caught and swallowed with `continue`
   - Processing continues with partial/empty corrections
   - Users receive "cleaned" transcripts that are actually uncorrected
   - No visibility into degraded quality

2. **Correction Application Limitations (Lines 455-489)**
   - Word boundary regex `\b` doesn't handle punctuation adjacency
   - `"Altman."` won't match correction for `"Altman"`
   - Fillers like `", um,"` fail to match
   - Case-sensitive matching misses variants

3. **No Success Rate Tracking**
   - `corrections_found` vs `corrections_applied` divergence not surfaced
   - No warning when most corrections fail to apply
   - Misleading "cleaned" output when corrections don't apply

### Medium Priority Issues

4. **Limited Speaker Identification Sampling (Lines 561-570)**
   - Only samples first and last chunks
   - Cold opens/outros can dominate the sample
   - No guard against all speakers mapping to same name

5. **Line-Based Chunking (Lines 257-291)**
   - Can cut mid-sentence on very long lines
   - Less critical due to large chunk sizes (180K-900K chars)

## Refactoring Plan

### Phase 1: Error Handling & Metrics (Critical)

#### Task 1.1: Add Failure Tracking to Phase 1 Analysis

**File:** `thestill/core/transcript_cleaning_processor.py`

**Changes:**
```python
# In _analyze_and_correct method
chunks_processed = 0
chunks_failed = 0

for i, chunk in enumerate(chunks):
    try:
        # ... existing LLM call ...
        chunks_processed += 1
    except Exception as e:
        chunks_failed += 1
        logger.error(f"Error analyzing chunk {i+1}: {e}")
        # Don't continue silently - track the failure

# After loop
failure_rate = chunks_failed / len(chunks) if chunks else 0
if failure_rate > 0.5:
    raise TranscriptCleaningError(
        f"Phase 1 failed: {chunks_failed}/{len(chunks)} chunks failed to process"
    )
elif chunks_failed > 0:
    logger.warning(f"Phase 1 degraded: {chunks_failed}/{len(chunks)} chunks failed")
```

**Metrics additions:**
- `phase1_chunks_failed: int`
- `run_status: Literal["success", "degraded", "failed"]`

#### Task 1.2: Add Corrections Success Rate Metric

**File:** `thestill/models/podcast.py`

**Changes to `TranscriptCleaningMetrics`:**
```python
@property
def corrections_success_rate(self) -> float:
    """Percentage of corrections that were successfully applied"""
    if self.phase1_corrections_found == 0:
        return 1.0  # No corrections needed = 100% success
    return self.phase1_5_corrections_applied / self.phase1_corrections_found

@property
def corrections_skipped(self) -> int:
    """Number of corrections that failed to apply"""
    return self.phase1_corrections_found - self.phase1_5_corrections_applied
```

**File:** `thestill/core/transcript_cleaning_processor.py`

**Changes to `clean_transcript`:**
```python
# After Phase 1.5
success_rate = applied_count / len(corrections) if corrections else 1.0
if success_rate < 0.5 and len(corrections) > 5:
    logger.warning(
        f"Low correction success rate: {applied_count}/{len(corrections)} "
        f"({success_rate:.0%}) corrections applied"
    )
```

---

### Phase 2: Correction Application Hardening (High Priority)

#### Task 2.1: Improve Regex Pattern for Punctuation Handling

**File:** `thestill/core/transcript_cleaning_processor.py`

**Current code:**
```python
escaped = re.escape(original)
pattern = rf"\b{escaped}\b"
```

**Proposed change:**
```python
escaped = re.escape(original)
# Use lookarounds that handle punctuation adjacency
# (?<![A-Za-z]) = not preceded by letter
# (?![A-Za-z]) = not followed by letter
pattern = rf"(?<![A-Za-z]){escaped}(?![A-Za-z])"
```

**Test cases to add:**
- `"Altman."` should match correction for `"Altman"`
- `"(OpenAI)"` should match correction for `"OpenAI"`
- `"Language"` should NOT match correction for `"La"`

#### Task 2.2: Add Case-Insensitive Mode for Fillers

**Changes:**
```python
def _apply_corrections(self, transcript_text: str, corrections: List[Dict]) -> tuple[str, int]:
    # ...
    for correction in sorted_corrections:
        original = correction.get("original", "")
        corrected = correction.get("corrected", "")
        correction_type = correction.get("type", "")

        if not original:
            continue

        escaped = re.escape(original)
        pattern = rf"(?<![A-Za-z]){escaped}(?![A-Za-z])"

        # Use case-insensitive for fillers (um, uh, like, you know)
        flags = re.IGNORECASE if correction_type == "filler" else 0

        new_text, count = re.subn(pattern, corrected, corrected_text, flags=flags)
        # ...
```

#### Task 2.3: Track Skipped Corrections

**Changes:**
```python
def _apply_corrections(self, transcript_text: str, corrections: List[Dict]) -> tuple[str, int, List[Dict]]:
    """
    Returns:
        Tuple of (corrected text, applied count, skipped corrections list)
    """
    skipped = []
    # ...
    for correction in sorted_corrections:
        # ...
        new_text, count = re.subn(pattern, corrected, corrected_text, flags=flags)
        if count > 0:
            corrected_text = new_text
            applied_count += 1
        else:
            skipped.append(correction)

    if skipped:
        logger.debug(f"Skipped {len(skipped)} corrections that didn't match")

    return corrected_text, applied_count, skipped
```

---

### Phase 3: Speaker Identification Improvement (Medium Priority)

#### Task 3.1: Sample Multiple Windows

**File:** `thestill/core/transcript_cleaning_processor.py`

**Current code:**
```python
if len(chunks) > 2:
    sample_text = chunks[0] + "\n\n[... middle content omitted ...]\n\n" + chunks[-1]
```

**Proposed change:**
```python
if len(chunks) > 3:
    # Sample first (intro), middle (main content), and last (outro)
    middle_idx = len(chunks) // 2
    sample_text = (
        chunks[0] +
        "\n\n[... content omitted ...]\n\n" +
        chunks[middle_idx] +
        "\n\n[... content omitted ...]\n\n" +
        chunks[-1]
    )
    logger.debug(f"Using 3 sample chunks (first, middle, last) of {len(chunks)} for speaker ID")
elif len(chunks) == 3:
    sample_text = "\n\n".join(chunks)
elif len(chunks) == 2:
    sample_text = chunks[0] + "\n\n" + chunks[1]
else:
    sample_text = transcript_text
```

#### Task 3.2: Add Guard Against Degenerate Mappings

**Changes:**
```python
def _identify_speakers(self, ...) -> Dict[str, str]:
    # ... existing code ...

    speaker_mapping = result.get("speaker_mapping", {})

    # Validate mapping quality
    if speaker_mapping:
        unique_names = set(speaker_mapping.values())
        if len(unique_names) == 1 and len(speaker_mapping) > 1:
            # All speakers mapped to same name - likely wrong
            logger.warning(
                f"All {len(speaker_mapping)} speakers mapped to same name "
                f"'{list(unique_names)[0]}' - keeping placeholders"
            )
            return {}

    return speaker_mapping
```

---

### Phase 4: Testing (Ongoing)

#### Task 4.1: Add Punctuation-Adjacent Correction Tests

**File:** `thestill/tests/test_deterministic_post_processor.py`

```python
class TestApplyCorrections:
    def test_correction_with_trailing_period(self, processor):
        """Correction should apply even when followed by period."""
        transcript = "I met Altman. He was nice."
        corrections = [{"type": "spelling", "original": "Altman", "corrected": "Altmann"}]

        result, count = processor._apply_corrections(transcript, corrections)

        assert "Altmann." in result
        assert count == 1

    def test_correction_with_parentheses(self, processor):
        """Correction should apply inside parentheses."""
        transcript = "The company (OpenAi) is great."
        corrections = [{"type": "spelling", "original": "OpenAi", "corrected": "OpenAI"}]

        result, count = processor._apply_corrections(transcript, corrections)

        assert "(OpenAI)" in result
        assert count == 1

    def test_filler_removal_with_commas(self, processor):
        """Filler words with surrounding commas should be removed."""
        transcript = "So, um, I think it works."
        corrections = [{"type": "filler", "original": ", um,", "corrected": ","}]

        result, count = processor._apply_corrections(transcript, corrections)

        assert "So, I think" in result
        assert count == 1
```

#### Task 4.2: Add Metrics Validation Tests

```python
class TestMetricsTracking:
    def test_corrections_success_rate_computed(self, processor):
        """Metrics should compute correction success rate."""
        # ... test implementation

    def test_low_success_rate_warning(self, processor, caplog):
        """Low correction success rate should log warning."""
        # ... test implementation
```

#### Task 4.3: Add Integration Test with Problematic Transcript

```python
class TestProblematicTranscripts:
    @pytest.fixture
    def problematic_transcript(self):
        """Fixture with known edge cases."""
        return {
            "segments": [
                {"speaker": "SPEAKER_00", "text": "Welcome to, um, the show.", "start": 0},
                {"speaker": "SPEAKER_01", "text": "Thanks Altman.", "start": 5},
                # ... more segments with edge cases
            ],
            "metadata": {"language": "en", "audio_file": "test.wav"}
        }

    def test_all_corrections_applied(self, processor, problematic_transcript):
        """All expected corrections should be applied."""
        # ... test implementation
```

---

### Phase 5: Optional Enhancements (Lower Priority)

#### Task 5.1: Sentence-Aware Chunking for Long Lines

**Only implement if we observe actual truncation issues.**

```python
def _chunk_transcript(self, text: str) -> List[str]:
    # ... existing code ...

    for line in lines:
        # Split very long lines at sentence boundaries
        if len(line) > 10000:
            sentences = re.split(r'(?<=[.!?])\s+', line)
            for sentence in sentences:
                # ... add to chunks
        else:
            # ... existing logic
```

---

## Implementation Order

| Priority | Task | Estimated Effort | Dependencies |
|----------|------|------------------|--------------|
| 1 | Task 1.1: Failure tracking | 30 min | None |
| 2 | Task 1.2: Success rate metric | 20 min | Task 1.1 |
| 3 | Task 2.1: Punctuation regex | 30 min | None |
| 4 | Task 2.2: Case-insensitive fillers | 15 min | Task 2.1 |
| 5 | Task 2.3: Track skipped corrections | 20 min | Task 2.1 |
| 6 | Task 4.1: Punctuation tests | 30 min | Task 2.1 |
| 7 | Task 3.1: Multi-window sampling | 20 min | None |
| 8 | Task 3.2: Degenerate mapping guard | 15 min | Task 3.1 |
| 9 | Task 4.2: Metrics tests | 30 min | Task 1.2 |
| 10 | Task 4.3: Integration tests | 45 min | All above |
| 11 | Task 5.1: Sentence chunking | 30 min | Optional |

**Total estimated effort:** ~4-5 hours

---

## Success Criteria

1. **Phase 1 failures surface clearly** - No more silent degradation
2. **Correction success rate visible** - Metrics show expected vs applied
3. **Punctuation-adjacent corrections work** - `"Altman."` matches `"Altman"`
4. **Fillers removed regardless of case** - `"Um"` and `"um"` both handled
5. **Speaker mapping validated** - Degenerate mappings rejected
6. **Test coverage increased** - All edge cases have tests

---

## Rollback Plan

All changes are backward compatible. If issues arise:
1. Revert to previous `transcript_cleaning_processor.py`
2. Metrics additions are additive (won't break existing code)
3. Test additions don't affect production

---

## References

- Original code: `thestill/core/transcript_cleaning_processor.py`
- Test file: `thestill/tests/test_deterministic_post_processor.py`
- Metrics model: `thestill/models/podcast.py`
- Staff engineer review: Conversation on 2025-11-26
