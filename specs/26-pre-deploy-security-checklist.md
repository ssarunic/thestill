# Pre-Deploy Security Checklist

**Status**: 📋 Planned (2026-04-23)
**Created**: 2026-04-23
**Updated**: 2026-04-23
**Priority**: High (gate for every production deployment)

## Overview

A reusable, LLM-runnable checklist that must return **GO** before any code
merges to `main` or deploys to a public environment. It is the regression
harness for the fixes tracked in
[25-security-audit-and-hardening.md](25-security-audit-and-hardening.md):
every Critical/High finding there has at least one check here.

The checklist lives in two forms:

1. **LLM prompt** (below) — run against the branch diff + full file context
   via a security-reviewer agent. Cheapest way to catch regressions in PRs.
2. **Runtime smoke test** (below) — a handful of curls/requests executed
   against a running instance. Catches things the LLM can't see
   (header behaviour, proxy config, live SSRF).

The output is a markdown table and a single **GO / NO-GO** verdict. A
`FAIL` on any Critical or High item is a blocker.

## Goals

1. One canonical document that both humans and LLMs can run before deploy.
2. Every item traces back to a spec #25 finding (or a general best
   practice) so failures point at a concrete fix.
3. Zero-setup: the prompt is self-contained and can be pasted into any
   agent with filesystem + shell access.
4. Fast enough to run on every PR, not just releases.

## Non-goals

- Replacing SAST tools (bandit, semgrep) — they can and should run
  alongside this checklist; this doc is the narrative, prioritised layer.
- Fuzzing or pen-testing. Those live elsewhere.
- Automating remediation. The checklist reports; humans fix.

## When to run

- **Every PR that touches** `thestill/web/**`, `thestill/mcp/**`,
  `thestill/webhook/**`, `thestill/core/media_source.py`,
  `thestill/core/audio_downloader.py`, `thestill/core/feed_manager.py`,
  `thestill/utils/path_manager.py`, `thestill/utils/jwt.py`,
  `thestill/services/auth_service.py`, `Dockerfile`, `docker-compose.yml`,
  `pyproject.toml`, or anything under `.env*`.
- **Before any deploy** to a public/multi-user environment.
- **After every dependency bump** involving `yt-dlp`, `feedparser`,
  `requests`, `fastapi`, `authlib`, `jwt`, or `pyyaml`.

## Output format

The checklist produces:

```
| #  | Check | PASS/FAIL | file:line | Remediation |
|----|-------|-----------|-----------|-------------|
| 1  | …     | PASS      | —         | —           |
| …  | …     | FAIL      | path:42   | do X        |

Verdict: GO | NO-GO
Reason (if NO-GO): <check numbers that failed>
```

Store runs under `reports/security/YYYY-MM-DD-<branch>.md` for audit trail.

## The LLM prompt

Paste this into a security-reviewer agent. It is intentionally
self-contained — do not assume the agent has read this spec or spec #25.

```
You are a security reviewer for the "thestill" podcast-transcription
pipeline (Python + FastAPI + MCP + SQLite). Read the diff between the
target branch and `main`, plus any files the diff touches. For every
check below, produce a PASS/FAIL verdict by actually opening the files
and verifying — do not guess. On FAIL, cite file:line, explain the
exploit, and propose a concrete fix. Do NOT mark PASS if you could not
verify.

# Secrets & supply chain
1.  No API keys, tokens, JWT secrets, OAuth client secrets, private keys,
    webhook secrets, or `.env` files are present in the diff or git
    history. Check `git log -p -- .env* **/*.pem **/*.key` and grep the
    diff for regex matches against: AWS access keys (AKIA…), OpenAI
    (`sk-…`), Anthropic (`sk-ant-…`), Google service accounts,
    ElevenLabs, and generic 32+ char hex/base64 values assigned to
    variables named `*secret*`, `*token*`, `*key*`, `*password*`.
2.  A lockfile exists (`uv.lock`, `poetry.lock`, or hashed
    `requirements.txt`) and is up to date with `pyproject.toml`. No
    floor-only pins on `yt-dlp`, `feedparser`, `pyyaml`, `requests`,
    `fastapi`, `authlib`, `jwt`. Run `pip-audit` (or equivalent) and
    report any CVEs.
3.  `.gitignore` excludes `.env`, `data/`, `*.db`, `venv/`, `reports/`,
    and any `*_secret*` / `*credentials*` files.

# External input handling (RSS / audio / YouTube)
4.  All XML parsing on untrusted input uses `defusedxml`. No plain
    `xml.etree.ElementTree`, no `lxml.etree` without
    `resolve_entities=False`, no `minidom`. Grep for the bad imports.
5.  Every outbound HTTP fetch that uses a user-supplied URL (RSS feed,
    episode audio, YouTube URL, webhook callback) resolves the hostname
    and rejects private, loopback, link-local, multicast, and reserved
    IP ranges — including on redirects. Look for a shared helper (e.g.
    `utils/url_guard.py`) that does this via `ipaddress.ip_address`.
6.  Audio downloads enforce both a header-based `content-length` cap AND
    a streaming byte cap. Downloaded files have magic-byte validation
    before being passed to `ffprobe` / `ffmpeg`.
7.  Feed URLs are scheme-checked (`http`/`https` only) before being
    handed to `feedparser` or `requests`.

# Command execution
8.  No `shell=True`, no `os.system`, no `subprocess` call with a
    string argument constructed from user input. All `subprocess`
    invocations pass a list.
9.  `ffmpeg` / `ffprobe` / `whisper` / `yt-dlp` never receive raw
    RSS-provided strings as file paths without a `--` separator and
    path canonicalization. Filenames pass an allowlist regex.

# Web surface
10. Auth cookies set `secure=True`, `httponly=True`,
    `samesite` ∈ {`lax`, `strict`}. No `secure=False` except behind
    an explicit `ENV=dev` guard.
11. CORS: no wildcard origins, methods, or headers when
    `allow_credentials=True`. Origins come from an env var, not
    hardcoded.
12. A security-headers middleware is installed and emits:
    Content-Security-Policy (strict; no `unsafe-inline` in
    `script-src`), Strict-Transport-Security, X-Content-Type-Options,
    X-Frame-Options, Referrer-Policy.
13. `/docs`, `/redoc`, `/openapi.json` are disabled or auth-gated in
    production (controlled by `ENV` / config).
14. Every mutating endpoint has rate limiting and a request-body size
    cap. Auth and webhook endpoints have per-IP throttles.
15. No endpoint reflects `X-Forwarded-*` headers into URLs or logs
    unless the request originates from a trusted-proxy IP allowlist.

# JWT / OAuth
16. Every JWT decode path verifies signature AND `exp`. No
    `verify_signature=False` except inside a helper explicitly named
    `_unsafe_peek_*`, and only called on already-verified tokens.
17. The JWT secret is loaded from an env var and startup fails if
    missing. No runtime auto-generation in multi-user mode.
18. The OAuth callback validates `state`, rejects reused states, and
    builds the redirect URI from config — not from request headers.

# Webhooks
19. Signature verification is mandatory: the server returns 401 when
    the webhook-secret env var is unset (outside an explicit dev
    guard). Uses `hmac.compare_digest`, not `==`.
20. Webhook payloads are size-capped and timestamp-checked (reject if
    `|now - t| > 5 min`) to block replay.

# Storage & filesystem
21. All file paths built from DB slugs or RSS-provided names are
    re-validated at point of use: regex allowlist AND
    `path.resolve().is_relative_to(data_root)`.
22. SQLite repositories use parameterized queries only. Grep for
    `.execute(` with string concatenation / `%` / f-strings in
    `thestill/repositories/`.
23. No `pickle.load`, no `yaml.load` (must be `yaml.safe_load`),
    no `eval` / `exec` on external input.

# LLM integration
24. Transcripts, feed text, and user-supplied content are injected
    into prompts inside explicit delimiters and labelled untrusted.
    Summarize / clean / digest flows run in a tool-less context
    (no MCP mutation tools bound).
25. MCP mutation tools (`remove_*`, `download_*`, `transcribe_*`,
    `summarize`) have per-session quotas and require confirmation for
    bulk operations.
26. No secret values (API keys, tokens, cookies) are passed into LLM
    prompts or logged in structured logs. Verify the redaction list in
    `web/middleware/logging_middleware.py`.

# Logging / observability
27. Structured logs redact the keys `token`, `code`, `state`,
    `authorization`, `api_key`, `secret`, `password` on both query
    params and request bodies. Exception messages are sanitized: only
    the exception type is logged at INFO; full message at DEBUG only.
28. No CRLF / control characters flow from feed titles or descriptions
    into logs. Strip before every `logger.*` call.

# Container / deploy
29. Dockerfile pins the base image by digest (`@sha256:…`), runs as
    non-root, copies no secrets, and respects `.dockerignore` (which
    must exclude `.env`, `data/`, `.git/`).
30. `docker-compose.yml` does not mount host secrets as bind mounts
    and does not expose internal ports (MCP, DB) on `0.0.0.0`.

Output: a markdown table with columns `# | Check | PASS/FAIL |
file:line | Remediation`, followed by `Verdict: GO | NO-GO` and, if
NO-GO, the list of failing check numbers. Fail the deploy if any
Critical (1-9, 16-20, 29) or High (10-15, 21-28, 30) check is FAIL.
```

## Runtime smoke test

Run after the LLM prompt passes, against a running instance (staging
preferred; localhost acceptable for PR-time checks). These catch what
static review can't.

```bash
# Route reference (verify against thestill/web/app.py before editing).
# A 404/405 from any check below is a FAIL - the smoke test is hitting
# a stale path and the security control is not actually exercised.
#
#   OAuth start:    GET  /api/auth/google/login          (302 -> Google)
#   Add podcast:    POST /api/commands/add               body {"url": "..."}, auth-gated
#   Webhook ingest: POST /webhook/elevenlabs/speech-to-text

# S1 — Host-header injection on OAuth start
# The endpoint replies with a 302; the attacker-controlled hostname
# would surface in the Location header (via _get_redirect_uri), not
# the body. We therefore check the Location header and the body both,
# and treat 404/405 as a FAIL because it means the smoke test is
# hitting a stale path and the security control is not exercised.
S1_STATUS=$(curl -s -o /tmp/s1.body -w "%{http_code}" \
  -H "Host: evil.com" \
  "http://localhost:8000/api/auth/google/login")
S1_LOCATION=$(curl -sI -H "Host: evil.com" \
  "http://localhost:8000/api/auth/google/login" | awk '/^[Ll]ocation:/ {print $2}')
if [ "$S1_STATUS" = "404" ] || [ "$S1_STATUS" = "405" ]; then
  echo "FAIL: endpoint missing (HTTP $S1_STATUS) - update smoke test"
elif echo "$S1_LOCATION $(cat /tmp/s1.body)" | grep -qi "evil.com"; then
  echo FAIL
else
  echo PASS
fi
# Must NOT reflect evil.com into the redirect URI or response body.

# S2 — SSRF: cloud metadata
# /api/commands/add requires auth; set AUTH_HEADER to a valid session
# cookie or bearer token before running. An unauthenticated 401/403 is
# not a useful signal - the SSRF guard must run after auth.
S2_BODY=$(curl -s -X POST http://localhost:8000/api/commands/add \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d '{"url":"http://169.254.169.254/latest/meta-data/"}')
if echo "$S2_BODY" | grep -qi "meta-data\|iam"; then
  echo "FAIL: cloud-metadata contents returned"
elif echo "$S2_BODY" | grep -qi "unsafe\|refused\|invalid\|blocked"; then
  echo PASS
else
  echo "FAIL: guard did not refuse ($S2_BODY)"
fi
# Must refuse with an explicit error, NOT silently succeed.

# S3 — SSRF: loopback
S3_BODY=$(curl -s -X POST http://localhost:8000/api/commands/add \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
  -d '{"url":"http://127.0.0.1:8000/api/"}')
echo "$S3_BODY" | grep -qi "unsafe\|refused\|invalid\|blocked" \
  && echo PASS || echo "FAIL ($S3_BODY)"
# Must refuse.

# S4 — XXE in RSS
cat > /tmp/xxe.xml <<'XML'
<?xml version="1.0"?>
<!DOCTYPE rss [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<rss><channel><title>&xxe;</title></channel></rss>
XML
# Host /tmp/xxe.xml locally (e.g. `python3 -m http.server 9000`) and
# POST that URL to /api/commands/add (with auth). /etc/passwd contents
# must not appear in responses, logs, or disk artifacts.

# S5 — Webhook without secret
# Unset ELEVENLABS_WEBHOOK_SECRET and DEV_ALLOW_UNSIGNED_WEBHOOKS, then:
S5_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/webhook/elevenlabs/speech-to-text \
  -H "Content-Type: application/json" \
  -d '{"status":"ok","transcription_id":"forged","transcription":{"text":"FORGED"}}')
[ "$S5_STATUS" = "401" ] && echo PASS || echo "FAIL (HTTP $S5_STATUS)"
# 404/405 means the smoke test is hitting a stale path - fix the path
# before re-running. 401 means the fail-closed guard is working.

# S6 — Webhook replay
# With ELEVENLABS_WEBHOOK_SECRET set, generate a valid HMAC-SHA256
# signature over `<ts>.<body>` using a timestamp 10 min in the past,
# then POST to /webhook/elevenlabs/speech-to-text with header
# `ElevenLabs-Signature: t=<old-ts>,v0=<sig>`. Must return 401.

# S7 — /docs on prod
ENV=production thestill server &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
# Must return 404 or 401, not 200.

# S8 — Security headers
curl -sI http://localhost:8000/ | grep -iE \
  'content-security-policy|strict-transport-security|x-content-type-options|x-frame-options'
# All four must appear.

# S9 — Body size cap
head -c 5242880 /dev/urandom > /tmp/big.bin   # 5 MB
S9_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/webhook/elevenlabs/speech-to-text \
  --data-binary @/tmp/big.bin)
[ "$S9_STATUS" = "413" ] && echo PASS || echo "FAIL (HTTP $S9_STATUS)"
# Must return 413 (payload too large). A 401 from signature verification
# happening before the size check is also a FAIL - the size cap must
# run first or in parallel so a signed attacker cannot memory-DoS.

# S10 — Oversize audio download
# Add a feed pointing to a server that advertises content-length
# > MAX_AUDIO_BYTES. Download must abort without writing the file.
```

Record `PASS`/`FAIL` per step in the same report file as the LLM
output. Verdict is `GO` iff every LLM check and every smoke step
reports `PASS`.

## Maintenance

- When a spec #25 item is closed, confirm the corresponding check
  here still exists and is worded tightly enough to catch a
  regression. If the finding introduced a new primitive (e.g. the
  `url_guard` module), update the relevant check to reference it by
  name.
- New features that touch external input, subprocess, auth, or file
  paths should add a check here in the same PR.
- Quarterly: re-run the full spec #25 audit against `main` and diff
  the findings. Anything new becomes a new row in spec #25's finding
  table and, if warranted, a new check here.

## Success criteria

1. Running the LLM prompt against `main` returns `GO`.
2. The 10-step smoke test passes against a staging instance.
3. Every Critical/High finding in spec #25 is covered by at least
   one check here.
4. The checklist runs in under 10 minutes end-to-end, so it is cheap
   enough to gate every PR touching the sensitive paths listed under
   "When to run".
