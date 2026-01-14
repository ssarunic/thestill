# Spec: WhisperX Chunk-Level Progress Tracking

## Goal

Capture WhisperX's stdout progress output (`Progress: XX.XX%...`) and convert it to real-time progress callbacks, enabling granular transcription progress tracking.

## Background

WhisperX internally splits audio into ~30-second chunks and processes them in batches. When `print_progress=True`, it outputs:

```
Progress: 12.50%...
Progress: 25.00%...
```

This output can be intercepted by replacing `sys.stdout` with a custom writer that parses the pattern and invokes a callback.

## Progress Percentage Allocation

**With diarization enabled:**

| Stage | % Range |
|-------|---------|
| Loading model | 0-5% |
| Transcribing | 5-25% (chunk-based, 20% range) |
| Aligning | 25-30% |
| Diarizing | 30-95% (time-based estimation) |
| Formatting | 95-100% |

**Without diarization:**

| Stage | % Range |
|-------|---------|
| Loading model | 0-5% |
| Transcribing | 5-85% (chunk-based, 80% range) |
| Aligning | 85-95% |
| Formatting | 95-100% |

## Implementation Steps

### Step 1: Create `StdoutProgressCapture` utility class

**File:** `thestill/utils/stdout_capture.py` (new file)

```python
class StdoutProgressCapture:
    """Context manager to intercept stdout and parse progress patterns."""

    def __init__(self, pattern: re.Pattern, callback: Callable[[float], None]):
        ...

    def __enter__(self) -> Self: ...
    def __exit__(self, *args) -> None: ...
    def write(self, text: str) -> None: ...
    def flush(self) -> None: ...
```

Features:

- Intercepts `sys.stdout`
- Passes through all output to original stdout (maintains console output)
- Parses text against provided regex pattern
- Calls callback with captured progress value
- Thread-safe restoration of stdout on exit

### Step 2: Update `WhisperXTranscriber` to use capture

**File:** `thestill/core/whisper_transcriber.py`

Changes to `transcribe_audio()` method:

1. Calculate progress range based on `self.enable_diarization`
2. Create callback that scales WhisperX's 0-100% to allocated range
3. Wrap `self._model.transcribe()` call with `StdoutProgressCapture`
4. Update subsequent stage percentages to match new allocation

### Step 3: Update `DiarizationProgressMonitor` ranges

**File:** `thestill/core/whisper_transcriber.py`

Update default values in `__init__`:

- `progress_base_pct`: 30 (was 10)
- `progress_range_pct`: 65 (was 80) → covers 30-95%

### Step 4: Add tests

**File:** `tests/test_stdout_capture.py` (new file)

Test cases:

- Pattern matching extracts correct values
- Callback invoked on match
- stdout passthrough works
- Context manager restores stdout on normal exit
- Context manager restores stdout on exception

## Files Changed

| File | Action |
|------|--------|
| `thestill/utils/stdout_capture.py` | Create |
| `thestill/core/whisper_transcriber.py` | Modify |
| `tests/test_stdout_capture.py` | Create |

## Risks & Mitigations

1. **Risk:** WhisperX changes output format
   - **Mitigation:** Pattern is simple (`Progress: XX.XX%`), unlikely to change. If it does, only one regex to update.

2. **Risk:** Concurrent stdout writes from other threads
   - **Mitigation:** stdout replacement is per-process, but WhisperX runs synchronously. Console output still visible.

3. **Risk:** Performance overhead from stdout interception
   - **Mitigation:** Negligible - just string matching on print calls, transcription is the bottleneck.
