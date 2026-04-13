# Pylint Fixes Plan

This document outlines the pylint issues found in the thestill codebase and a prioritized plan to address them.

## Current Status

- **Pylint Score**: 9.19/10 (improved from 9.06)
- **Total Issues**: ~900 warnings/errors across the codebase
- **E-level Errors**: 0 (all resolved!)
- **Date**: 2025-01-15
- **Last Updated**: 2025-01-15

### Completed Fixes

1. ✅ Fixed E1125 in `cli.py` - Added missing `language` argument to transcript cleaning calls
2. ✅ Fixed E0102 in `cli.py` - Refactored duplicate `stream_callback` function definition
3. ✅ Fixed E1131 in `task_handlers.py` - Updated pylint to Python 3.12 (recognizes `X | Y` union syntax)
4. ✅ Fixed E1120 in `cli.py` - Disabled `no-value-for-parameter` (Click framework false positive)
5. ✅ Added `create_llm_provider_from_config()` helper to reduce 15-line boilerplate in 9 places
6. ✅ Updated `.pylintrc` `py-version` from 3.9 to 3.12
7. ✅ Removed dead `_clean_transcript_with_llm` method from `whisper_transcriber.py` (51 lines)
8. ✅ Fixed E1125 in `mcp/tools.py` - Added missing `language` argument
9. ✅ Fixed E1101 in `mcp/tools.py` - Corrected `PodcastService` method names
10. ✅ Fixed E1101 in `mcp/resources.py` - Corrected `list_podcasts` → `get_podcasts`
11. ✅ Fixed E1128 in `test_media_source.py` - Fixed assignment-from-none warning
12. ✅ Fixed E1135 in `test_service_contracts.py` - Fixed unsupported-membership-test warnings
13. ✅ Fixed E1123 in `test_cleaning.py` and `test_formatter.py` - Updated to new API signatures
14. ✅ Suppressed false positive E1101 errors in `google_transcriber.py` and `llm_provider.py` (third-party libs)
15. ✅ Suppressed E0401 import-error for optional `librosa` dependency in `parakeet_transcriber.py`

## Issue Summary

| Code | Count | Severity | Description |
|------|-------|----------|-------------|
| W0621 | 460 | Low | Redefining name from outer scope |
| W0718 | 136 | Medium | Catching too general exception |
| C0415 | 117 | Low | Import outside toplevel |
| R0917 | 65 | Low | Too many positional arguments |
| R0801 | 61 | Low | Duplicate code |
| W0613 | 47 | Low | Unused argument |
| W0611 | 43 | Low | Unused import |
| W0107 | 30 | Low | Unnecessary pass statement |
| W0212 | 25 | Medium | Access to protected member |
| W0612 | 23 | Low | Unused variable |
| R1705 | 22 | Low | Unnecessary else after return |
| W1309 | 15 | Low | f-string without interpolation |
| R1702 | 10 | Medium | Too many nested blocks |
| E1101 | 10 | High | Member has no attribute |
| C0302 | 8 | Medium | Too many lines in module |
| E1125 | 7 | High | Missing mandatory keyword argument |
| E1131 | 5 | High | Unsupported binary operation |

## Priority Levels

- **P0 (Critical)**: Errors that may cause runtime failures
- **P1 (High)**: Issues affecting code quality and maintainability
- **P2 (Medium)**: Best practice violations
- **P3 (Low)**: Style and convention issues

---

## TODO List

### P0: Critical Errors (Fix Immediately)

- [x] **Fix E1125: Missing mandatory `language` argument in transcriber calls** ✅
  - Location: `cli.py:636`, `cli.py:967`, `cli.py:983`
  - Issue: Transcriber API was refactored to require `language` parameter but callers weren't updated
  - Fix: Added `language=podcast.language` to all transcriber method calls

- [x] **Fix E1120: No value for parameter in function call** ✅
  - Location: `cli.py:2382`
  - Issue: Click framework false positive - decorators handle parameter injection
  - Fix: Disabled `no-value-for-parameter` in `.pylintrc`

- [x] **Fix E0102: Function already defined** ✅
  - Location: `cli.py:596`
  - Issue: `stream_callback` defined as `None` then redefined as function
  - Fix: Refactored to define `_stream_chunk` function, then assign conditionally

- [x] **Fix E1131: Unsupported binary operation with `|`** ✅
  - Location: `task_handlers.py:268`, `task_handlers.py:561`, `task_handlers.py:619`
  - Issue: pylint was configured for Python 3.9, doesn't recognize 3.10+ union syntax
  - Fix: Updated `.pylintrc` `py-version` to 3.12

### P0.5: Remaining Critical Errors (Not in original scope)

- [x] **Fix E1125 in `whisper_transcriber.py:592`** ✅
  - Issue: Legacy `_clean_transcript_with_llm` method uses outdated `TranscriptCleaner` API
  - Fix: Removed dead method entirely (51 lines)

- [x] **Fix E1125 in `mcp/tools.py:909`** ✅
  - Issue: Missing `language` argument in MCP tools transcription call
  - Fix: Added `language=podcast.language` to all cleaning calls

- [x] **Fix E1101 in `mcp/tools.py` and `mcp/resources.py`** ✅
  - Issues: `PodcastService` method names don't match (`list_podcasts` vs `get_podcasts`)
  - Fix: Changed all `list_podcasts()` calls to `get_podcasts()`

- [x] **Fix E1123/E1128/E1135 in tests** ✅
  - Issues: Test files using outdated API signatures
  - Fix: Updated `test_cleaning.py`, `test_formatter.py`, `test_media_source.py`, `test_service_contracts.py`

### P1: High Priority (Next Sprint)

- [ ] **Split cli.py into smaller modules**
  - Current: 2382 lines (limit: 1000)
  - Target: Split into logical command groups
  - Suggested structure:
    - `cli/main.py` - Main CLI entry point and context
    - `cli/podcast_commands.py` - add, list, remove, refresh
    - `cli/processing_commands.py` - download, downsample, transcribe
    - `cli/transcript_commands.py` - clean-transcript, summarize
    - `cli/facts_commands.py` - facts subcommands
    - `cli/server_commands.py` - server, status
    - `cli/evaluation_commands.py` - evaluate-transcript, evaluate-postprocess

- [ ] **Split google_transcriber.py into smaller modules**
  - Current: 2479 lines (limit: 1000)
  - Suggested structure:
    - `google_transcriber/base.py` - Base class and utilities
    - `google_transcriber/sync.py` - Synchronous transcription
    - `google_transcriber/async_transcriber.py` - Async/GCS transcription
    - `google_transcriber/chunked.py` - Chunked transcription logic

- [ ] **Fix E1101: Member has no attribute (10 occurrences)**
  - Investigate each case - may indicate API changes or type annotation issues
  - Add proper type stubs if needed

### P2: Medium Priority (Backlog)

- [ ] **Replace broad exception catching (W0718: 136 occurrences)**
  - Replace `except Exception:` with specific exceptions
  - Keep broad catches only at top-level error boundaries
  - Add logging before re-raising where appropriate
  - Priority files:
    - `cli.py` (20+ occurrences)
    - `media_source.py` (9 occurrences)
    - `google_transcriber.py` (8+ occurrences)
    - `elevenlabs_transcriber.py` (7+ occurrences)

- [ ] **Fix protected member access (W0212: 25 occurrences)**
  - Review each case for proper encapsulation
  - Either make members public or add proper accessor methods

- [ ] **Reduce nested blocks (R1702: 10 occurrences)**
  - Refactor deeply nested code using early returns
  - Extract helper functions where appropriate
  - Locations:
    - `cli.py:1733`
    - `feed_manager.py:489`
    - `google_transcriber.py:621`, `google_transcriber.py:678`

- [ ] **Fix raise-missing-from warnings (W0707: 8 occurrences)**
  - Change `raise SomeError()` to `raise SomeError() from e`
  - Preserves exception chain for better debugging

### P3: Low Priority (Nice to Have)

- [ ] **Move imports to top of file (C0415: 117 occurrences)**
  - Most are intentional lazy imports for performance
  - Review and document which are intentional
  - Consider using `TYPE_CHECKING` block for type-only imports

- [ ] **Remove unused imports (W0611: 43 occurrences)**
  - Run `autoflake` or manually remove
  - Be careful with imports used only for type hints

- [ ] **Remove unused arguments (W0613: 47 occurrences)**
  - Some may be required by interface contracts
  - Prefix truly unused args with `_` to suppress warning
  - Priority: `feed_manager.py` has 5 unused `storage_path` arguments

- [ ] **Remove unused variables (W0612: 23 occurrences)**
  - Use `_` for intentionally unused values
  - Remove truly dead code

- [ ] **Remove unnecessary pass statements (W0107: 30 occurrences)**
  - Replace with `...` in abstract methods or
  - Add docstring instead of pass

- [ ] **Fix unnecessary else after return (R1705: 22 occurrences)**
  - Refactor to remove else and de-indent code
  - Improves readability

- [ ] **Fix f-strings without interpolation (W1309: 15 occurrences)**
  - Change `f"text"` to `"text"`

- [ ] **Fix redefined names from outer scope (W0621: 460 occurrences)**
  - Rename local variables to avoid shadowing
  - Many are false positives from Click decorators
  - Consider disabling for specific patterns

- [ ] **Reduce function arguments (R0917: 65 occurrences)**
  - Group related parameters into dataclasses
  - Use `**kwargs` where appropriate
  - Consider builder pattern for complex constructors

- [ ] **Address duplicate code (R0801: 61 occurrences)**
  - Extract common patterns into shared utilities
  - Review after other refactoring is complete

---

## Suggested Approach

### Phase 1: Fix Critical Errors (1-2 hours)

1. Fix all E-level errors (E1125, E1120, E0102, E1131)
2. Run tests to ensure nothing is broken
3. Commit with message: `fix: resolve critical pylint errors`

### Phase 2: Module Splitting (4-6 hours)

1. Create `cli/` package structure
2. Move command functions to appropriate modules
3. Update imports and entry points
4. Run tests after each move
5. Commit with message: `refactor: split cli.py into command modules`

### Phase 3: Exception Handling (2-3 hours)

1. Identify appropriate specific exceptions for each catch
2. Update exception handling in priority files
3. Add logging where needed
4. Commit with message: `refactor: use specific exception types`

### Phase 4: Cleanup (2-3 hours)

1. Remove unused imports, variables, arguments
2. Fix unnecessary pass/else statements
3. Fix f-string issues
4. Commit with message: `chore: cleanup pylint warnings`

---

## Configuration Updates

Consider updating `.pylintrc` to disable certain checks that are intentional:

```ini
[MESSAGES CONTROL]
disable=
    # Intentional lazy imports for performance
    import-outside-toplevel,
    # Click decorators cause many false positives
    redefined-outer-name,
    # Already using type hints, less critical
    missing-function-docstring,
```

---

## Metrics Target

| Metric | Current | Target |
|--------|---------|--------|
| Pylint Score | 9.06/10 | 9.50/10 |
| E-level errors | 25+ | 0 |
| W-level warnings | 800+ | <200 |
| Lines in cli.py | 2382 | <500 |
| Lines in google_transcriber.py | 2479 | <500 |

---

## Notes

- Many W0621 (redefined name) warnings are false positives from Click's decorator pattern
- C0415 (import outside toplevel) is often intentional for lazy loading
- Some W0613 (unused argument) are required by interface contracts
- Consider running `pylint --generate-rcfile` to create a baseline config
