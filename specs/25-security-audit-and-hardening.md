# Security Audit and Hardening

**Status**: 🚧 Active development (Phases 1, 2 shipped; Phase 3 safe batch shipped 2026-04-23)
**Created**: 2026-04-23
**Updated**: 2026-04-23 (Phase 3 Medium findings 3.1, 3.4, 3.5, 3.7, 3.9 closed; 3.2/3.3/3.6/3.8 deferred)
**Priority**: High (Critical/High findings must land before any public/multi-user deployment; Medium/Low can follow)

## Overview

A whole-codebase security audit of thestill identified 27 findings spanning
XML parsing, SSRF, webhook authentication, LLM prompt injection, cookie/JWT
hygiene, CORS/headers, subprocess exposure, resource limits, and supply-chain
posture. This spec is the working document for **closing** those findings —
each is listed with its location, exploit, remediation, and a checkbox so we
can track progress PR by PR.

Companion: [26-pre-deploy-security-checklist.md](26-pre-deploy-security-checklist.md)
— the pre-deploy review prompt/checklist that enforces these fixes don't
regress.

## Goals

1. Eliminate every Critical and High finding before any internet-facing deploy.
2. Establish defense-in-depth primitives (`defusedxml`, SSRF guard,
   security-headers middleware, body-size cap, fail-closed webhook) that
   future features inherit by default.
3. Land Medium findings as a follow-up hardening PR inside the same quarter.
4. Keep Low/Info findings on the radar without blocking shipping.
5. Pair every fix with a regression test so the pre-deploy checklist
   (spec #26) has something to run against.

## Non-goals

- Full pen-test or third-party audit. This is a first-pass self-audit.
- Migrating storage (SQLite → Postgres) for auth reasons. The current
  single-user mode doesn't need it; multi-user hardening is covered by
  spec #07.
- Runtime IDS/WAF deployment. Reverse proxy concerns live in spec #05.
- Replacing the MCP transport.

## Phases

Phases are ordered by urgency, not by implementation dependency. Within a
phase, items can land in any order.

### Phase 1 — Critical (blocks any deployment)

- [x] **1.1 XXE in RSS parsing.** ✅ Shipped.
  Swapped `xml.etree.ElementTree` for `defusedxml.ElementTree` in
  [media_source.py](../thestill/core/media_source.py); added
  `defusedxml>=0.7.1` to [pyproject.toml](../pyproject.toml).
  Regression tests: [test_xxe.py](../tests/unit/security/test_xxe.py).
- [x] **1.2 SSRF on user-supplied URLs.** ✅ Shipped.
  New [utils/url_guard.py](../thestill/utils/url_guard.py) validates
  scheme + resolves DNS + refuses private / loopback / link-local /
  cloud-metadata / reserved addresses (IPv4 and IPv6). Integrated at
  every outbound call driven by user input:
  [media_source.py](../thestill/core/media_source.py) (RSS fetch +
  Apple-podcast redirect resolver, plus a `_GuardedHTTPAdapter` that
  re-validates on every redirect),
  [audio_downloader.py](../thestill/core/audio_downloader.py),
  [external_transcript_downloader.py](../thestill/core/external_transcript_downloader.py),
  and [feed_manager.py](../thestill/core/feed_manager.py)
  (guarded fetch before `feedparser.parse`). Escape hatch:
  `URL_GUARD_ALLOWLIST` env var for self-hosted Dalston.
  Regression tests: [test_url_guard.py](../tests/unit/security/test_url_guard.py).
- [x] **1.3 Webhook signature bypass when secret unset.** ✅ Shipped.
  [webhooks.py](../thestill/web/routes/webhooks.py) now fails closed:
  returns **401** when `ELEVENLABS_WEBHOOK_SECRET` is unset (escape hatch:
  `DEV_ALLOW_UNSIGNED_WEBHOOKS=1` for local dev). Signature verification
  also checks the signed timestamp is within ±5 min of wall-clock to block
  replay, validates header parseability before `hmac.compare_digest`, and
  supports versioned `v0`…`v9` signatures.
  Regression tests: [test_webhook_auth.py](../tests/unit/security/test_webhook_auth.py).
- [x] **1.4 LLM prompt injection from transcripts → tool abuse.** ✅ Shipped
  (defence-in-depth pass).
  Confirmed that `post_processor.TranscriptSummarizer`, `TranscriptCleaner`,
  and `FactsExtractor` call `chat_completion` / `generate_structured`
  with **no tools bound** — even a successful jailbreak cannot invoke MCP
  mutation tools. Added
  [utils/prompt_safety.py](../thestill/utils/prompt_safety.py) with
  `wrap_untrusted()` + `UNTRUSTED_CONTENT_PREAMBLE`, and wired it into
  every system/user prompt that carries transcript text. Attacker-embedded
  sentinels inside transcripts are stripped before wrapping.
  Regression tests: [test_prompt_safety.py](../tests/unit/security/test_prompt_safety.py).
  Follow-up: apply the same wrapping to
  [segmented_transcript_cleaner.py](../thestill/core/segmented_transcript_cleaner.py)
  per-segment batch prompts (Phase 3 hardening).

### Phase 2 — High (must land before this spec closes)

- [x] **2.1 Cookie `secure=False` hardcoded.** ✅ Shipped.
  [auth.py](../thestill/web/routes/auth.py) `_set_auth_cookie` now reads
  `Config.cookie_secure` (defaults True; toggle via
  `COOKIE_SECURE=false` for local http dev). `samesite="strict"` on the
  auth cookie; `oauth_state` cookie also honours `cookie_secure`.
  Regression tests: [test_auth_hardening.py](../tests/unit/security/test_auth_hardening.py).
- [x] **2.2 JWT `verify_signature=False` helper.** ✅ Shipped.
  [jwt.py](../thestill/utils/jwt.py): the old `get_token_expiry` is
  renamed to `_unsafe_peek_token_expiry` (observability / refresh-decision
  use only). A new `get_token_expiry(token, secret_key, algorithm)` now
  verifies the signature. `is_token_expiring_soon` requires the secret
  key, so a forged JWT can no longer pretend to be fresh. Callers and
  tests updated.
- [x] **2.3 Rate limiting.** ✅ Shipped.
  New [web/middleware/rate_limit.py](../thestill/web/middleware/rate_limit.py)
  with a thread-safe in-process sliding-window limiter. Applied as a router
  dependency on `/api/auth/*` and `/webhook/*`, and as an explicit
  quota-gate at the top of every mutating MCP tool
  ([mcp/tools.py](../thestill/mcp/tools.py)). Limits tunable via
  `RATE_LIMIT_*` env vars.
  Regression tests: [test_rate_limit.py](../tests/unit/security/test_rate_limit.py).
- [x] **2.4 OAuth redirect trusts `X-Forwarded-*`.** ✅ Shipped.
  `_get_redirect_uri` honours `X-Forwarded-*` only when
  `request.client.host` ∈ `Config.trusted_proxies`. Otherwise it
  prefers `Config.public_base_url` and falls back to the ASGI-reported
  URL (which cannot be spoofed via the Host header). Regression tests
  include a Host-header injection attempt.
- [x] **2.5 CORS wildcard with credentials.** ✅ Shipped.
  [app.py](../thestill/web/app.py) now skips `CORSMiddleware` entirely
  when `allowed_origins` is empty in production. When present, method
  and header lists are explicit (`GET, POST, PUT, PATCH, DELETE, OPTIONS`;
  `Accept, Authorization, Content-Type, ...`). Dev environment keeps
  the Vite ports as a fallback only when `allowed_origins` is empty.
  Regression tests: [test_app_hardening.py](../tests/unit/security/test_app_hardening.py).
- [x] **2.6 Subprocess / yt-dlp / ffmpeg exposure.** ✅ Shipped.
  [duration.py](../thestill/utils/duration.py) now canonicalises the
  path (`Path.resolve()`) and calls `assert_audio_file` before invoking
  `ffprobe`, with the `--` separator to terminate option parsing.
  Remaining yt-dlp supply-chain work (lockfile, digest-pinned Docker
  image) lives in Phase 3 / deploy config — tracked in items 3.8 and
  5.1.
- [x] **2.7 Unbounded audio download + no integrity check.** ✅ Shipped.
  [audio_downloader.py](../thestill/core/audio_downloader.py) enforces
  `MAX_AUDIO_BYTES` both via `content-length` pre-check and cumulative
  stream-byte count, and deletes the partial file on overflow. After
  download, [utils/audio_integrity.py](../thestill/utils/audio_integrity.py)
  magic-byte-validates against MP3 / AAC / WAV / OGG / FLAC / M4A —
  polyglots, HTML error pages, and zip bombs are refused before any
  ffmpeg pass. Regression tests: [test_audio_integrity.py](../tests/unit/security/test_audio_integrity.py).
- [x] **2.8 Docs endpoints + verbose errors on prod.** ✅ Shipped.
  [app.py](../thestill/web/app.py) sets `docs_url=None, redoc_url=None,
  openapi_url=None` unless `ENVIRONMENT=development` or `ENABLE_DOCS=true`.
  A generic exception handler returns `{"detail": "Internal Server Error"}`
  in production (full detail in logs only) and the exception-class name
  in dev, so upstream errors (e.g. from authlib) never leak to clients.
  Regression tests: [test_app_hardening.py](../tests/unit/security/test_app_hardening.py).

### Phase 3 — Medium (follow-up hardening PR)

- [x] **3.1 Security headers middleware.** ✅ Shipped (safe batch).
  New [web/middleware/security_headers.py](../thestill/web/middleware/security_headers.py)
  emits CSP (no `unsafe-inline` in `script-src`, `frame-ancestors 'none'`,
  strict origin allowlists), `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, and `Referrer-Policy:
  strict-origin-when-cross-origin` on every response.
  `Strict-Transport-Security: max-age=31536000; includeSubDomains`
  is emitted in production only, so local http dev doesn't get pinned
  to a TLS cert the box doesn't have.
  Regression tests: [test_security_headers.py](../tests/unit/security/test_security_headers.py).
- [ ] **3.2 Markdown/transcript XSS in frontend.** (Deferred — needs live
  React audit against the Vite dev server.)
  Audit [web/frontend/](../thestill/web/frontend/) renderers. Ensure no
  `dangerouslySetInnerHTML` without DOMPurify with a conservative
  allowlist (no `<script>`, no `on*` handlers, no `javascript:` URIs).
  Test with a transcript containing `<img src=x onerror=alert(1)>`.
- [ ] **3.3 Path-traversal regression surface.**
  [path_manager.py:143,155](../thestill/utils/path_manager.py#L143). At
  every path build from a DB slug, assert `^[a-z0-9][a-z0-9-]*$` AND
  `path.resolve().is_relative_to(data_root)`. Fail loud.
- [x] **3.4 Feed URL scheme validation.** ✅ Shipped (safe batch).
  [feed_manager.py](../thestill/core/feed_manager.py) `add_podcast`
  parses with `urllib.parse` and refuses anything outside `{http, https}`
  before the media-source factory runs. The SSRF guard catches it too,
  but this makes the failure fast and localised at the entry point and
  covers the yt-dlp path that bypasses `requests`.
  Regression tests: [test_feed_scheme.py](../tests/unit/security/test_feed_scheme.py).
- [x] **3.5 Secrets in logs / error bodies.** ✅ Shipped (safe batch).
  New [utils/log_safety.py](../thestill/utils/log_safety.py) exposes
  `redact_mapping` + a `log_safety_processor` wired at the END of the
  structlog processor chain — so every `logger.*` call across the whole
  codebase has sensitive keys (`token`, `secret`, `password`,
  `authorization`, `cookie`, `api_key`, `code`, `state`, `session`, …)
  replaced with `[redacted]` automatically. Query-param logging in
  [logging_middleware.py](../thestill/web/middleware/logging_middleware.py)
  also runs `redact_mapping` explicitly as belt-and-braces. The verbose
  OAuth error paths in [auth.py](../thestill/web/routes/auth.py) were
  already fixed in Phase 2 item 2.8.
  Regression tests: [test_log_safety.py](../tests/unit/security/test_log_safety.py).
- [ ] **3.6 SQLite task-queue race.** (Deferred — touches background
  pipeline; wants a two-worker integration test.)
  [queue_manager.py](../thestill/core/queue_manager.py) and
  [task_manager.py](../thestill/web/task_manager.py). Use
  `BEGIN IMMEDIATE` + conditional `UPDATE ... WHERE status='pending'`
  for claim-next-job. Enable WAL + `busy_timeout`. Test with two workers
  claiming the same row.
- [x] **3.7 Request body size cap.** ✅ Shipped (safe batch).
  New [web/middleware/body_size.py](../thestill/web/middleware/body_size.py)
  rejects POST/PUT/PATCH with `Content-Length` above the per-route cap
  (413). Webhook routes get the tighter `MAX_WEBHOOK_BODY_BYTES` cap;
  everything else falls back to the same value as the default. GET
  requests pass through unchanged. Returns a real `JSONResponse` — the
  initial implementation tried `raise HTTPException` from middleware,
  which Starlette's `BaseHTTPMiddleware` doesn't auto-convert.
  Regression tests: [test_body_size.py](../tests/unit/security/test_body_size.py).
- [ ] **3.8 yt-dlp supply-chain.** (Deferred — needs tooling choice.)
  Hash-lock dependencies via `uv lock` / `pip-compile --generate-hashes`.
  Add automated update PRs via Dependabot/Renovate. Document a fast-patch
  policy: critical yt-dlp CVEs within 48 h.
- [x] **3.9 Log injection via CRLF.** ✅ Shipped (safe batch).
  Solved inside [utils/log_safety.py](../thestill/utils/log_safety.py):
  the `log_safety_processor` walks every event dict and escapes control
  characters (`\r`, `\n`, `\x00`, etc.; tab preserved) so an RSS title
  like `"Evil\\r\\n{\"level\":\"critical\"}"` cannot forge a log line in
  JSON consumers. Applying this as a structlog processor — instead of
  chasing every `logger.*` call site — means the mitigation is global and
  regression-proof.
  Regression tests in [test_log_safety.py](../tests/unit/security/test_log_safety.py)
  (shared with 3.5).
  review).

### Phase 4 — Low (opportunistic)

- [ ] **4.1 Per-restart JWT secret in single-user mode.**
  [auth_service.py:103](../thestill/services/auth_service.py#L103).
  Require `JWT_SECRET_KEY` env var; fail startup if missing.
- [ ] **4.2 JWT revocation path.**
  Short TTL (≤ 1 h) + refresh tokens, or a server-side `jti` deny-list.
  Logout must invalidate on the server.
- [ ] **4.3 Centralize URL regex patterns.**
  [media_source.py:223](../thestill/core/media_source.py#L223),
  [youtube_downloader.py:57-65](../thestill/core/youtube_downloader.py#L57-L65).
  Move patterns to one module; ban unbounded alternation/backrefs; unit
  tests guard against ReDoS.
- [ ] **4.4 Webhook payloads on disk.**
  [webhooks.py:171-180](../thestill/web/routes/webhooks.py#L171-L180).
  `chmod 0600` on write; document sensitivity; consider at-rest
  encryption for multi-tenant deployments.

### Phase 5 — Info / best practice

- [ ] **5.1 Docker base-image pin.**
  Pin `python:3.12-slim` by digest (`@sha256:…`); rebuild weekly in CI.
- [ ] **5.2 Secret scanning pre-commit.**
  Add `gitleaks` or `trufflehog` to
  [.pre-commit-config.yaml](../.pre-commit-config.yaml) and as a CI job.
- [ ] **5.3 PostgreSQL password policy (future).**
  Placeholder: if we ever migrate off SQLite, enforce a password policy
  and disallow empty passwords in startup validation.

## Finding reference

Full finding table with exploits and severity ratings lives below. Keep
this section in sync with phase checkboxes above so a PR closing a phase
item also marks the finding resolved.

| # | Sev | Finding | Location | Phase | Status |
|---|-----|---------|----------|-------|--------|
| 1  | Critical | XXE in RSS parsing | media_source.py:33,716,839,1113 | 1.1 | ✅ |
| 2  | Critical | SSRF on RSS/audio/YouTube fetch | media_source.py:551,564; audio_downloader.py:104-135 | 1.2 | ✅ |
| 3  | Critical | Webhook auth bypass when secret unset | webhooks.py:217-226 | 1.3 | ✅ |
| 4  | Critical | LLM prompt injection via transcripts | services/* + mcp/tools.py | 1.4 | ✅ |
| 5  | High     | Cookie `secure=False` hardcoded | auth.py:58-65 | 2.1 | ✅ |
| 6  | High     | JWT `verify_signature=False` helper | jwt.py:96-117 | 2.2 | ✅ |
| 7  | High     | No rate limiting on auth / webhook / MCP | auth.py, webhooks.py, mcp/tools.py | 2.3 | ✅ |
| 8  | High     | OAuth trusts `X-Forwarded-*` | auth.py:48-53 | 2.4 | ✅ |
| 9  | High     | CORS wildcard + credentials | app.py:256-266 | 2.5 | ✅ |
| 10 | High     | Subprocess / yt-dlp / ffmpeg exposure | duration.py:205-219; downsample.py | 2.6 | ✅ |
| 11 | High     | Unbounded audio download + no integrity check | audio_downloader.py:166-176 | 2.7 | ✅ |
| 12 | High     | `/docs`, `/redoc` exposed on prod | app.py:247-248 | 2.8 | ✅ |
| 13 | Medium   | No security headers (CSP/HSTS/XFO/XCTO) | app.py middleware | 3.1 | ✅ |
| 14 | Medium   | Transcript/Markdown XSS in frontend | web/frontend/* | 3.2 | ☐ |
| 15 | Medium   | Path traversal via slug regression | path_manager.py:143,155 | 3.3 | ☐ |
| 16 | Medium   | Non-http feed URL schemes accepted | feed_manager.py:152 | 3.4 | ✅ |
| 17 | Medium   | Secrets in error paths / logs | auth_service.py:201; auth.py:200; logging_middleware.py:76 | 3.5 | ✅ |
| 18 | Medium   | SQLite queue race / duplicate processing | queue_manager.py, task_manager.py | 3.6 | ☐ |
| 19 | Medium   | No request body size limit | app.py | 3.7 | ✅ |
| 20 | Medium   | yt-dlp supply-chain / RCE surface | pyproject.toml | 3.8 | ☐ |
| 21 | Medium   | Log injection via CRLF in feed titles | logger.* call sites | 3.9 | ✅ |
| 22 | Low      | Per-restart JWT secret in single-user | auth_service.py:103 | 4.1 | ☐ |
| 23 | Low      | No JWT revocation list | utils/jwt.py | 4.2 | ☐ |
| 24 | Low      | URL regex ReDoS footgun | media_source.py:223; youtube_downloader.py:57-65 | 4.3 | ☐ |
| 25 | Low      | Webhook payloads unencrypted on disk | webhooks.py:171-180 | 4.4 | ☐ |
| 26 | Info     | Dockerfile base-image not digest-pinned | Dockerfile | 5.1 | ☐ |
| 27 | Info     | No secret-scanning pre-commit | .pre-commit-config.yaml | 5.2 | ☐ |

## Gates

- **Gate A — Phase 1 complete.** All four Critical findings closed with
  regression tests. Blocks any public or multi-user deployment.
- **Gate B — Phase 2 complete.** All High findings closed. Blocks enabling
  `docs_url` behind auth and any "share a URL publicly" feature.
- **Gate C — Phase 3 complete.** Follow-up hardening PR merged. Blocks
  closing this spec.
- **Gate D — Phase 4/5 complete.** Housekeeping; spec moves to ✅ Complete.

## Success criteria

1. The pre-deploy checklist in spec #26 returns **GO** on `main`.
2. Every phase checkbox above is checked with a linked PR.
3. Regression tests for each Critical/High finding live in
   `tests/security/` and run in CI.
4. No Critical/High finding reappears in a subsequent audit pass.

## Rollout

Land fixes in small, reviewable PRs — one phase item per PR where
possible. Each PR:

1. References this spec (`spec #25, item X.Y`).
2. Includes a regression test.
3. Updates the checkbox in this spec and the "Status" column in the
   finding table.
4. Updates [26-pre-deploy-security-checklist.md](26-pre-deploy-security-checklist.md)
   if the check's wording needs to be tightened.

When Gate A and B are both green, run the spec #26 checklist end-to-end
against a staging environment and record the output under
`reports/security/` for audit trail.

## Open questions

- Do we want a dedicated bug-bounty or responsible-disclosure channel
  before opening multi-user hosting (spec #07)?
- Should MCP mutation tools require an interactive confirmation step
  for bulk operations, or is a quota sufficient?
- Is there appetite for running the downloader (`yt-dlp` + ffmpeg) in a
  separate container with no network access to internal services?
