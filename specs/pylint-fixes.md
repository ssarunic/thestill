# Pylint Fixes Plan

This document outlines the pylint issues found in the thestill codebase and a prioritized plan to address them.

## Current Status

- **Pylint Score**: 9.06/10
- **Total Issues**: ~1,100 warnings/errors across the codebase
- **Date**: 2025-01-15

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

- [ ] **Fix E1125: Missing mandatory `language` argument in transcriber calls**
  - Location: `cli.py:636`, `cli.py:967`, `cli.py:983`
  - Issue: Transcriber API was refactored to require `language` parameter but callers weren't updated
  - Fix: Add `language` parameter to all transcriber method calls

- [ ] **Fix E1120: No value for parameter in function call**
  - Location: `cli.py:2382`
  - Issue: Function called without required `ctx` and `config` arguments
  - Fix: Ensure proper arguments are passed to the function

- [ ] **Fix E0102: Function already defined**
  - Location: `cli.py:596`
  - Issue: Duplicate function definition
  - Fix: Remove or rename the duplicate function

- [ ] **Fix E1131: Unsupported binary operation with `|`**
  - Location: `task_handlers.py:268`, `task_handlers.py:561`, `task_handlers.py:619`
  - Issue: Using `|` operator on incompatible types (likely union type syntax issue)
  - Fix: Use `Union[]` from typing or fix the type annotations

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
