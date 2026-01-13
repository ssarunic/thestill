# DRY Refactoring Plan

**Status**: ✅ Complete
**Created**: 2026-01-13
**Updated**: 2026-01-13
**Priority**: Medium (improves maintainability, no new features)

## Progress Summary

| Phase | Status | Lines Saved |
|-------|--------|-------------|
| Phase 1: Web API Response Helpers | ✅ Complete | ~40 lines |
| Phase 2: Task Handler Boilerplate | ✅ Complete | ~35 lines |
| Phase 3: Audio Duration Utilities | ✅ Complete | ~15 lines |
| Phase 4: Device Resolution Utilities | ✅ Complete | ~45 lines |
| Phase 5: CLI Config Check Decorator | ✅ Complete (19/19 commands) | ~57 lines |
| Phase 6: HTTP Error Helpers | ✅ Complete (merged into Phase 1) | ~20 lines |

**Total lines eliminated**: ~210+ lines of duplicated code

---

## Overview

This plan addresses DRY (Don't Repeat Yourself) principle violations identified across the codebase. The goal is to eliminate duplicated code while maintaining backward compatibility and avoiding breaking changes.

**Guiding Principles**:

- Each refactoring task is atomic and independently deployable
- Tests must pass after each task
- No functional changes — only structural improvements
- Balance DRY with KISS (Keep It Simple) — avoid over-abstraction

---

## Phase 1: Web API Response Helpers (High Impact, Low Risk)

**Estimated effort**: 1-2 hours
**Files affected**: 5 files in `thestill/web/`

### Problem

Every API endpoint repeats the same response structure:

```python
return {
    "status": "ok",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    # ... data fields
}
```

This pattern appears **14+ times** across route files.

### Solution

Create `thestill/web/responses.py` with helper functions:

```python
# thestill/web/responses.py
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

def api_response(data: Dict[str, Any], status: str = "ok") -> Dict[str, Any]:
    """Wrap data in standard API response envelope."""
    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data,
    }

def paginated_response(
    items: List[Any],
    total: int,
    offset: int,
    limit: int,
    items_key: str = "items",
) -> Dict[str, Any]:
    """Create paginated API response with standard pagination fields."""
    has_more = offset + len(items) < total
    return api_response({
        items_key: items,
        "count": len(items),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    })
```

### Tasks

1. **P1-1**: Create `thestill/web/responses.py` with `api_response()` and `paginated_response()`
2. **P1-2**: Add unit tests in `tests/test_web_responses.py`
3. **P1-3**: Refactor `thestill/web/routes/health.py` to use helpers
4. **P1-4**: Refactor `thestill/web/routes/api_podcasts.py` to use helpers
5. **P1-5**: Refactor `thestill/web/routes/api_episodes.py` to use helpers
6. **P1-6**: Refactor `thestill/web/routes/api_dashboard.py` to use helpers
7. **P1-7**: Refactor `thestill/web/routes/api_commands.py` to use helpers

### Verification

```bash
./venv/bin/pytest tests/test_web_responses.py -v
./venv/bin/pytest tests/ -k "api" -v
```

---

## Phase 2: Task Handler Boilerplate (Medium Impact, Low Risk)

**Estimated effort**: 1 hour
**Files affected**: `thestill/core/task_handlers.py`

### Problem

All 5 task handlers repeat identical boilerplate:

```python
# Repeated in every handler
result = state.repository.get_episode(task.episode_id)
if not result:
    raise FatalError(f"Episode not found in database: {task.episode_id}")
podcast, episode = result

try:
    # ... handler-specific logic ...
except (FatalError, TransientError):
    raise
except Exception as e:
    classify_and_raise(e, context=f"... for {episode.title}")
```

### Solution

Extract common patterns into helper functions within the same file:

```python
# At top of task_handlers.py
from typing import Tuple
from ..models.podcast import Episode, Podcast

def _get_episode_or_fail(task: Task, state: "AppState") -> Tuple[Podcast, Episode]:
    """Get episode and podcast from task, raising FatalError if not found."""
    result = state.repository.get_episode(task.episode_id)
    if not result:
        raise FatalError(f"Episode not found in database: {task.episode_id}")
    return result
```

For error wrapping, use a context manager:

```python
from contextlib import contextmanager

@contextmanager
def handler_error_context(context_msg: str, default_transient: bool = True):
    """Context manager for consistent error handling in task handlers."""
    try:
        yield
    except (FatalError, TransientError):
        raise
    except Exception as e:
        classify_and_raise(e, context=context_msg, default_transient=default_transient)
```

### Tasks

1. **P2-1**: Add `_get_episode_or_fail()` helper function
2. **P2-2**: Add `handler_error_context()` context manager
3. **P2-3**: Refactor `handle_download()` to use helpers
4. **P2-4**: Refactor `handle_downsample()` to use helpers
5. **P2-5**: Refactor `handle_transcribe()` to use helpers
6. **P2-6**: Refactor `handle_clean()` to use helpers
7. **P2-7**: Refactor `handle_summarize()` to use helpers

### Verification

```bash
./venv/bin/pytest tests/ -k "handler" -v
# Manual test: run full pipeline via web UI
```

---

## Phase 3: Audio Duration Utilities (Medium Impact, Low Risk)

**Estimated effort**: 30 minutes
**Files affected**: 3 files

### Problem

Audio duration calculation exists in three places:

| Location | Function | Returns | Method |
|----------|----------|---------|--------|
| `thestill/utils/duration.py:119` | `get_audio_duration()` | seconds (int) | ffprobe |
| `thestill/core/transcriber.py:82` | `_get_audio_duration_minutes()` | minutes (float) | pydub |
| `thestill/core/whisper_transcriber.py:986` | `_get_audio_duration_seconds()` | seconds (float) | pydub |

### Solution

The existing `get_audio_duration()` in `duration.py` uses ffprobe which is more reliable. Add a minutes variant and deprecate the pydub-based methods:

```python
# Add to thestill/utils/duration.py
def get_audio_duration_minutes(audio_path: Union[str, Path]) -> float:
    """Get audio file duration in minutes using ffprobe."""
    seconds = get_audio_duration(audio_path)
    return seconds / 60.0 if seconds else 0.0
```

### Tasks

1. **P3-1**: Add `get_audio_duration_minutes()` to `thestill/utils/duration.py`
2. **P3-2**: Update `thestill/core/transcriber.py` to use `duration.get_audio_duration_minutes()`
3. **P3-3**: Update `thestill/core/whisper_transcriber.py` to use `duration.get_audio_duration()`
4. **P3-4**: Remove duplicate `_get_audio_duration_*` methods from transcriber classes

### Verification

```bash
./venv/bin/pytest tests/ -k "duration" -v
./venv/bin/thestill transcribe --dry-run  # Verify no import errors
```

---

## Phase 4: Device Resolution Utilities (Medium Impact, Medium Risk)

**Estimated effort**: 1 hour
**Files affected**: 3 files

### Problem

Device resolution logic (CUDA/MPS/CPU detection) is duplicated:

- `transcriber.py:90` — `_resolve_device()` (simple)
- `whisper_transcriber.py:650` — `_resolve_hybrid_devices()` (advanced, per-stage)

Both check `torch.cuda.is_available()` and `torch.backends.mps.is_available()`.

### Solution

Create `thestill/utils/device.py` with unified device resolution:

```python
# thestill/utils/device.py
from typing import Tuple
import torch

def is_cuda_available() -> bool:
    """Check if CUDA is available."""
    return torch.cuda.is_available()

def is_mps_available() -> bool:
    """Check if Apple Metal (MPS) is available."""
    return hasattr(torch.backends, "mps") and torch.backends.mps.is_available()

def resolve_device(device: str) -> str:
    """
    Resolve 'auto' device to actual device.

    Args:
        device: 'auto', 'cuda', 'mps', or 'cpu'

    Returns:
        Resolved device string
    """
    if device == "auto":
        if is_cuda_available():
            return "cuda"
        # MPS has issues with some models, default to CPU
        return "cpu"
    return device

def resolve_hybrid_devices(device: str) -> Tuple[str, str, str]:
    """
    Resolve device for multi-stage pipelines (transcription, alignment, diarization).

    On Mac with MPS: CPU for transcription (Faster-Whisper issues), MPS for rest.
    On CUDA: GPU for all stages.
    On CPU-only: CPU for all stages.

    Returns:
        Tuple of (transcription_device, alignment_device, diarization_device)
    """
    if device == "auto":
        if is_cuda_available():
            return ("cuda", "cuda", "cuda")
        elif is_mps_available():
            return ("cpu", "mps", "mps")
        return ("cpu", "cpu", "cpu")
    elif device == "mps":
        if is_mps_available():
            return ("cpu", "mps", "mps")
        return ("cpu", "cpu", "cpu")
    elif device == "cuda":
        if is_cuda_available():
            return ("cuda", "cuda", "cuda")
        return ("cpu", "cpu", "cpu")
    return (device, device, device)
```

### Tasks

1. **P4-1**: Create `thestill/utils/device.py` with device utilities
2. **P4-2**: Add unit tests in `tests/test_device.py`
3. **P4-3**: Update `thestill/core/transcriber.py` to use `device.resolve_device()`
4. **P4-4**: Update `thestill/core/whisper_transcriber.py` to use `device.resolve_hybrid_devices()`
5. **P4-5**: Remove duplicate device resolution methods from transcriber classes

### Verification

```bash
./venv/bin/pytest tests/test_device.py -v
./venv/bin/thestill transcribe --dry-run  # Verify imports work
```

### Risk Mitigation

- Device resolution is critical for transcription performance
- Test on both Mac (MPS) and Linux (CUDA/CPU) if possible
- Keep warning messages for fallback scenarios

---

## Phase 5: CLI Config Check Decorator (Low Impact, Low Risk)

**Estimated effort**: 30 minutes
**Files affected**: `thestill/cli.py`

### Problem

Every CLI command repeats the same null check:

```python
if ctx.obj is None:
    click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
    ctx.exit(1)
```

This appears **19 times** in cli.py.

### Solution

Create a decorator to wrap commands that require config:

```python
# Add near top of cli.py, after imports
import functools

def require_config(f):
    """Decorator to ensure CLIContext is loaded before command runs."""
    @click.pass_context
    @functools.wraps(f)
    def wrapper(ctx, *args, **kwargs):
        if ctx.obj is None:
            click.echo("❌ Configuration not loaded. Please check your setup.", err=True)
            ctx.exit(1)
        return ctx.invoke(f, *args, **kwargs)
    return wrapper
```

### Tasks

1. **P5-1**: Add `require_config` decorator to `cli.py`
2. **P5-2**: Refactor `add`, `remove`, `list` commands to use decorator
3. **P5-3**: Refactor `refresh`, `download`, `downsample` commands
4. **P5-4**: Refactor `transcribe`, `clean-transcript`, `summarize` commands
5. **P5-5**: Refactor remaining commands (`status`, `cleanup`, `facts`, etc.)

### Verification

```bash
./venv/bin/thestill --help
./venv/bin/thestill list
./venv/bin/thestill refresh --dry-run
```

---

## Phase 6: HTTP Error Helpers (Low Impact, Low Risk)

**Estimated effort**: 30 minutes
**Files affected**: Route files in `thestill/web/routes/`

### Problem

HTTPException raises are scattered with repeated messages:

```python
raise HTTPException(status_code=404, detail=f"Podcast not found: {podcast_slug}")
raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")
```

### Solution

Add error helpers to `thestill/web/responses.py`:

```python
from fastapi import HTTPException

def not_found(resource: str, identifier: str) -> HTTPException:
    """Raise 404 Not Found for a resource."""
    raise HTTPException(status_code=404, detail=f"{resource} not found: {identifier}")

def bad_request(message: str) -> HTTPException:
    """Raise 400 Bad Request."""
    raise HTTPException(status_code=400, detail=message)
```

### Tasks

1. **P6-1**: Add error helpers to `thestill/web/responses.py`
2. **P6-2**: Refactor `api_podcasts.py` to use error helpers
3. **P6-3**: Refactor `api_episodes.py` to use error helpers
4. **P6-4**: Refactor `api_commands.py` to use error helpers

### Verification

```bash
./venv/bin/pytest tests/ -k "api" -v
# Manual: test 404 responses via API
```

---

## Deferred / Out of Scope

These items were identified but deferred due to higher complexity or lower impact:

### Transcriber Model Loading Pattern

**Reason deferred**: Each transcriber has unique initialization requirements. Abstracting would add complexity without significant DRY benefit.

### Configuration Constants Consolidation

**Reason deferred**: Constants are provider-specific and rarely change. Moving to shared file would reduce locality of reference.

### Transcript Format Conversion

**Reason deferred**: Each provider returns different structures. A generic converter would need complex mapping logic that's harder to maintain than current inline conversions.

---

## Implementation Order

Recommended order based on impact and risk:

| Order | Phase | Impact | Risk | Effort |
|-------|-------|--------|------|--------|
| 1 | Phase 1: Web API Response Helpers | High | Low | 1-2h |
| 2 | Phase 2: Task Handler Boilerplate | Medium | Low | 1h |
| 3 | Phase 3: Audio Duration Utilities | Medium | Low | 30m |
| 4 | Phase 5: CLI Config Check Decorator | Low | Low | 30m |
| 5 | Phase 6: HTTP Error Helpers | Low | Low | 30m |
| 6 | Phase 4: Device Resolution | Medium | Medium | 1h |

**Total estimated effort**: 4-5 hours

---

## Rollback Plan

Each phase is independent. If issues arise:

1. Revert the specific commit for that phase
2. Other phases remain unaffected
3. No database migrations involved — pure code refactoring

---

## Success Criteria

- [ ] All existing tests pass after each phase
- [ ] No functional changes to API responses or CLI output
- [ ] Code coverage maintained or improved
- [ ] Reduced line count in affected files (target: -50 to -70 lines total)
- [ ] mypy passes with no new errors
