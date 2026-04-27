# Security operations

This is the runbook for security-relevant maintenance: dependency
upgrades, fast-patch response, and how the supply-chain protections
(spec #25) are wired together.

## Supply-chain controls in place

| Layer | Mechanism | Why |
|---|---|---|
| Python deps | [`uv.lock`](../uv.lock) — every package pinned with sha256 | A compromised PyPI publish can't ambush a fresh install: hash mismatch → install fails. |
| Python install | CI: `uv sync --frozen`. Docker: `pip wheel --require-hashes` | Both honour the lockfile; nothing in CI or in the shipped image floats. |
| Docker base images | All `FROM` lines digest-pinned (`@sha256:…`) in [Dockerfile](../Dockerfile) | Tags (`:slim`, `:latest`) are mutable. Without a digest, every build pulls "whatever Docker Hub points at right now". |
| Frontend deps | `package-lock.json` committed; `npm ci` in CI | npm-ecosystem equivalent of the Python lockfile. |
| Upgrade cadence | [Dependabot](../.github/dependabot.yml) — weekly Mondays for pip / npm / docker | Bot opens grouped minor/patch PRs; humans review and merge. Avoids drift while keeping a paper trail. |
| Secret scanning | [gitleaks pre-commit + CI](../.github/workflows/ci.yml) | Catches accidentally-committed API keys, JWTs, etc. on push. |

## Routine: weekly Dependabot review

Every Monday Dependabot opens grouped PRs for:

1. **pip minor + patch** — backend Python deps.
2. **npm minor + patch** — frontend deps.
3. **docker** — base-image digest bumps.

Workflow:

1. Inspect each PR's diff. Minor/patch should be small.
2. Wait for CI green (Python tests + Frontend tests + Docker build + Secret scan).
3. Merge. Dependabot rebases the others automatically.
4. Major bumps come as separate PRs and need a closer read — they're allowed to fail; close and ignore if a major upgrade isn't worth the migration cost yet.

## Fast-patch policy: critical CVE in `yt-dlp` (or any other dep)

`yt-dlp` is the highest-risk dep in this codebase: it parses untrusted
server responses and runs FFmpeg subprocesses on user-supplied URLs.
Any RCE or path-traversal CVE in `yt-dlp` is **patch within 48 h**.
Same response shape applies to any other Critical CVE in the dep tree.

### Detection sources

- GitHub Security Advisories on the repo (auto-enabled).
- Dependabot security alerts (separate from the routine weekly bumps).
- The `yt-dlp` GitHub Releases page — major CVEs typically have a
  same-day patch release.

### Response steps

1. **Confirm severity.** Read the advisory. Is it RCE? Affecting our
   call site? `yt-dlp` is invoked from
   [`thestill/core/youtube_downloader.py`](../thestill/core/youtube_downloader.py)
   with user-supplied URLs — assume yes unless the advisory explicitly
   excludes the YouTube/audio-extraction path we use.
2. **Bump the lockfile:**

   ```bash
   uv lock --upgrade-package yt-dlp
   ```

   Eyeball `uv.lock` to confirm only `yt-dlp` and its direct
   dependencies moved.
3. **Run the suite:**

   ```bash
   ./venv/bin/python -m pytest -q --ignore=tests/integration/pipeline
   ```

   Then `npx tsc --noEmit && npx vitest run` from
   `thestill/web/frontend/`.
4. **Open a PR titled `fix(security): bump yt-dlp to <version> for <CVE-id>`.**
   Reference the advisory in the body. Skip the routine-review queue —
   merge as soon as CI is green.
5. **Rebuild the Docker image** if running in production. The deploy
   process is owner-defined; the slim image build is a one-line
   `docker build -t thestill:slim --target slim .`.
6. **Annotate the spec.** If the CVE was particularly nasty, add a
   one-line entry under "Notable patched CVEs" below.

### Timing budget

| Step | Target time |
|---|---|
| Detection → assessed | < 6 h (during business hours) |
| PR open | < 24 h |
| Merged + deploy | < 48 h |

These targets assume normal staffing. Genuine all-hands-on-deck CVEs
(actively exploited, no workaround) get the full 48 h window if they
land overnight; otherwise faster is better.

## Notable patched CVEs

_None to date._ Add a one-line entry per fast-patch event with date +
CVE id + commit ref so we can reconstruct the supply-chain history
later.

## Adding a new third-party dep

1. Add to `pyproject.toml` (Python) or `package.json` (frontend).
2. Run `uv lock` or `npm install` — the lockfile rebuilds with the new
   dep + its transitive deps + their hashes.
3. Commit **both** the manifest and the lockfile in the same PR. Never
   commit one without the other; CI will fail when the lockfile is out
   of sync with the manifest.
4. If the dep is high-risk (parses untrusted input, runs subprocesses,
   does network IO), make sure it's on the radar for fast-patch
   policy — this is a judgement call, not a checklist.
