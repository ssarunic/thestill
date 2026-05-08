# Constitution

> The non-negotiables. Specs, plans, and code all defer to this file.
> If it stops fitting on one screen, prune before adding.

**Status**: 📖 Reference
**Created**: 2026-05-07
**Updated**: 2026-05-07

## How to use this file

- Read it before starting any non-trivial change.
- If a principle here conflicts with another spec or doc, this file wins —
  flag the conflict in the PR and amend whichever doc is wrong.
- Amendments require a PR that bumps `Updated` and explains the *why* in the
  commit message. No silent edits.

> **Numbering note.** Spec #00 is intentionally outside the normal
> `max + 1` rule (see [README.md](README.md)) — the constitution outranks
> the rest, so it sits ahead of #01.

## Principles

### 1. Layered architecture, one-way dependencies

`CLI → Services → Core → Repositories → Models`. Lower layers never import
upper layers. The CLI does no business logic; services don't open SQLite
cursors; core doesn't import Click; models do no I/O. New cross-layer
glue goes through dependency injection at the CLI boundary.
See [01-architecture.md](01-architecture.md).

### 2. Pipeline stages are atomic and idempotent

Every pipeline stage (refresh → download → downsample → transcribe →
clean → summarize, plus the entity continuation) must be safe to re-run.
Stages check for existing artifacts and resume; partial state lives on
`Episode`, not in memory. State transitions go through `EpisodeState`;
no out-of-band flags.

### 3. PathManager owns every path

No string concatenation, no hardcoded `data/` paths, no `os.path.join`
of artifact roots in business code. New artifact types add a
`PathManager` method first; tests use the real `PathManager` against
`tmp_path`.

### 4. Repositories own the database

Services and core never open a SQLite cursor or write raw SQL outside
`repositories/`. When raw SQL against `podcasts.db` is unavoidable,
never use `CURRENT_TIMESTAMP` — use `strftime` with `+00:00` ISO-8601,
because the schema stores explicit string timestamps and a mismatched
format silently corrupts ordering.

### 5. Pydantic at every external boundary

Anything entering or leaving the system — RSS feed, LLM response, HTTP
request, MCP tool argument, webhook payload — is parsed into a Pydantic
model before it touches business logic. Internal types are Pydantic
where validation matters, plain dataclasses or NamedTuples otherwise.

### 6. structlog only — no `print`

All logs go through `structlog.get_logger()` with structured kwargs
(`episode_id=...`), never f-strings inside the message. Every entry
point — CLI command, HTTP request, MCP tool, task worker — binds a
correlation ID (`command_id`, `request_id`, `mcp_request_id`,
`task_id`) so cross-layer traces reconstruct cleanly. Never log
secrets, tokens, full file contents, or PII.

### 7. No silent failures; classify every retryable error

No bare `except:` and no `except Exception: pass`. Every caught
exception is either re-raised or logged with `exc_info=True` and
converted to a domain exception from `utils/exceptions.py`. Pipeline
errors are classified `TransientError` (retry with backoff) or
`FatalError` (DLQ). New failure modes update
`core/error_classifier.py`, not the call site.
See [03-error-handling.md](03-error-handling.md).

### 8. Type hints on every public signature

`mypy thestill/` stays clean. Public functions, methods, and class
attributes carry type hints; trivial private one-liners may skip them.
Coverage target: 90%+. A PR that introduces new `mypy` errors is not
ready to merge.

### 9. Tests mock only the world's edge

Mock external APIs (HTTP, LLM, feedparser, yt-dlp); never mock the
code under test. Filesystem tests use `tmp_path` and the real
`PathManager`; database tests use a real SQLite file. Coverage
targets: 70% overall, 90% on `core/`. AAA structure, descriptive
names. See [04-testing.md](04-testing.md).

### 10. Specs are the source of truth

Plans and reference docs live in `specs/`, numbered, never
renumbered, never reused. Adding a spec means adding a row to
[README.md](README.md) in the same PR. The constitution is spec #00
and outranks everything else.

## Toolchain

- Python 3.9+; always invoked as `./venv/bin/python`,
  `./venv/bin/pytest`, or `./venv/bin/thestill` — never bare `python`
  or `pytest`.
- `make check` (black + isort + pylint + mypy + pytest) is the merge
  gate.
- Conventional commit messages: `feat:`, `fix:`, `refactor:`,
  `chore:`, `docs:`, `test:`. Include a scope where it adds clarity
  (`feat(web): …`).
- Secrets live in `.env`; never in code, logs, commits, or test
  fixtures.

## How this evolves

A principle stays here only if it is non-negotiable **and** enforced.
If a rule is being routinely violated, either fix the code or remove
the rule — a constitution nobody follows is worse than none. Detailed
how-to lives in the longer specs and in
[../docs/code-guidelines.md](../docs/code-guidelines.md); this file
names the rules and points there.
