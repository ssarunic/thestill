# Weekly dependency sweep

Triage every open Dependabot pull request and security alert for
`ssarunic/thestill`, land the ones that are proven safe, hand the rest to a
human with a clear reason, and file a short digest of what the upgraded
libraries newly offer that Thestill could use. Work autonomously and end with a
summary report.

This runbook is executed by a scheduled Claude cloud routine (Mondays,
10:00 UTC, two hours after Dependabot opens its PRs). It can also be run by
hand as `/dependency-sweep` from a local Claude Code session.

Prime directive: **never land a change that CI has not proven green.** When in
doubt, leave the PR open with a comment; a human closes the loop. Nothing here
is ever pushed to `main` directly. Every change reaches `main` through a PR
merge.

## 0. Tooling and access

- GitHub access comes in three interchangeable forms; use whichever is
  present. The examples below are written as `gh` commands, but every one has
  an equivalent in the other two forms.
  - `gh` CLI, when installed and `gh auth status` passes.
  - The GitHub MCP tools (`mcp__github__*`: `search_pull_requests`,
    `pull_request_read`, `merge_pull_request`, `issue_write`,
    `actions_list`, `actions_get`, ...). This is what the cloud routine has;
    `gh` is not installed there. Load them with
    `ToolSearch select:mcp__github__<name>`. Pass `expectedHeadSha` to
    `merge_pull_request` so a rebase between check and merge fails safely.
    Search results can be huge; request `fields` to keep them small.
  - `curl` against `https://api.github.com` with `GH_TOKEN` (or
    `GITHUB_TOKEN`) as a bearer token. In the cloud routine the token is
    proxy-injected and works for repo, PR, issue and Actions endpoints.
  - If none of the three works, STOP and report exactly which call failed.
    Do not guess at merges without API access.
- **Dependabot alerts endpoint.** The cloud routine's token is a GitHub App
  installation token and gets `403 Resource not accessible by integration`
  on `/dependabot/alerts` (confirmed 2026-09-25). When that happens, skip
  section 3's alert listing and dismissals, say so in the report, and rely
  on the fact that Dependabot security updates open a fix PR for every
  fixable alert, which section 2 handles. Do not try other tokens or
  work-arounds. (To enable alert access, a human adds a fine-grained PAT
  with "Dependabot alerts: read and write" as `DEPENDABOT_TOKEN` in the
  routine's environment; if `DEPENDABOT_TOKEN` is set, use it for the
  alerts endpoints only.)
- GitHub search endpoints have a low secondary rate limit; on a 403 rate
  limit, wait with a background `until` loop, not a foreground `sleep`.
- Confirm `uv --version`. If `uv` is missing, install it
  (`curl -LsSf https://astral.sh/uv/install.sh | sh`) before doing any Python
  lockfile work.
- You are on a fresh checkout of `main`. Do not run `git stash`, `git reset`,
  or `git add -A`; stage named files only. Do not create commits on `main`.

## 1. Discover

Collect three lists:

- **Open Dependabot PRs:** `gh pr list --author app/dependabot --state open
  --json number,title,headRefName,labels,mergeable,statusCheckRollup,body`.
- **Open security alerts:** `gh api repos/ssarunic/thestill/dependabot/alerts
  --paginate` filtered to `state == "open"`, with
  `security_vulnerability.first_patched_version`, `dependency.manifest_path`
  and the advisory summary.
- **Recently merged Dependabot PRs** since the last sweep: `gh pr list
  --author app/dependabot --state merged --search "merged:>=<date 8 days ago>"`
  plus anything you merge in this run. These feed the digest in section 5.

If all three lists are empty, post the "all clear" report (section 6) and
stop.

## 2. Classify each open PR

Read the PR body. Dependabot embeds the upstream release notes and changelog
excerpts there. Determine the version delta (patch / minor / major) and
whether the dependency is runtime or dev-only:

- Python runtime = `[project.dependencies]` in `pyproject.toml` and the
  `postgres`, `s3`, `ses`, `search`, `entities`, `web` extras (these are what
  the prod image installs; see `Dockerfile` `EXTRAS`).
- Python dev-only = the `dev` extra and the `local-transcription` extra (the
  latter is never installed in prod or CI; it exists for laptops running local
  Whisper/NeMo).
- npm: `dependencies` in `thestill/web/frontend/package.json` are runtime;
  `devDependencies` are dev-only. The frontend ships as a built bundle, so
  dev-only bumps still affect the build and are gated by CI's build + test.
- GitHub Actions and Docker digest bumps are infrastructure; treat as
  dev-only.

Read the CI status. Every Dependabot PR runs the full `CI` workflow: pytest
against real Postgres, ruff, vitest, a Playwright browser run against the
built bundle, a Docker slim build and gitleaks. If checks are still running,
wait for them (`gh pr checks <n> --watch --fail-fast`, up to 25 minutes).

Then apply exactly one of these outcomes:

### Merge

Merge with `gh pr merge <n> --squash --delete-branch` when **all** of:

- CI is fully green (no failed, cancelled or skipped required jobs).
- The PR is mergeable (no conflicts). If it conflicts, comment
  `@dependabot rebase`, and revisit it later in the run; if still conflicting
  at the end, leave it open and report.
- One of:
  - patch or minor bump (including the grouped `*-minor-patch` PRs), or
  - major bump of a **dev-only** dependency, or
  - major bump of a **runtime** dependency where you have read the upstream
    breaking-changes list and checked each item against this codebase with
    `grep -rn` on the affected symbols, and none apply. Say so in a PR
    comment listing what you grepped for before merging.

### Leave open for a human

Comment on the PR with a one-paragraph reason and leave it open when:

- CI is red or flaky and the failure is not obviously pre-existing on `main`.
  Quote the failing job and the first error lines in your comment.
- A runtime major bump has a breaking change that touches code here, or the
  changelog is too thin to judge.
- Merging would change behaviour in a way tests cannot see (auth libraries,
  crypto, LLM SDK response shapes, yt-dlp extractor behaviour). Name the risk.

Never "fix" a red Dependabot PR by pushing to its branch; Dependabot
force-pushes over it.

### Close

Close with `gh pr close <n> --comment "<reason>"` and, for majors, also
comment `@dependabot ignore this major version` when:

- The bump crosses a ceiling that `pyproject.toml` or `package.json` pins **on
  purpose with a comment** (today: `mistralai<3` because 2.x dropped the
  `Mistral` export; `mcp<3` until the next major is vetted;
  `whisperx<4`; `transformers<5.18`). Quote the comment in the close reason.
- The PR is superseded (a newer PR for the same dependency exists).

Do not close a PR merely because you could not evaluate it. That is "leave
open".

## 3. Handle security alerts that have no PR

For each open alert:

1. Decide applicability. Locate the package in `uv.lock` (which extra pulls it
   in) or `package-lock.json`, and grep the codebase for the vulnerable API or
   feature named in the advisory.
2. **Not applicable** (vulnerable feature or mode unused; e.g. an SSR-only
   advisory against our Vite SPA): dismiss with evidence:
   `gh api -X PATCH repos/ssarunic/thestill/dependabot/alerts/<n>
   -f state=dismissed -f dismissed_reason=not_used
   -f dismissed_comment="<what you checked>"`. The comment must cite the grep
   or file you inspected.
3. **Applicable, patched version exists within the current constraint:** fix it
   on a branch and open a PR.
   - Python: `git checkout -b deps/<pkg>-<version>`; then
     `uv lock --upgrade-package '<pkg>==<first patched version>'`. Pin the
     patched version explicitly: a bare `uv lock --upgrade-package <pkg>`
     jumps to the newest release, which can be a major (nemo-toolkit
     `>=2.0.0` resolves to 3.0.0 when the patch is 2.6.2). Check the diff of
     `uv.lock` shows only that package and its transitive changes. Commit
     only `uv.lock`; push; open a PR titled
     `fix(deps): bump <pkg> <old> -> <new> (GHSA-...)` whose body lists the
     alert numbers and the extra the package belongs to. Then treat it like a
     Dependabot PR in section 2 (wait for CI, merge when green).
   - If the pinned patched version cannot resolve because of a transitive
     conflict, try the newest release in the same major
     (`'<pkg>>=<patched>,<<next major>'`). If that fails too, do not force it
     and do not cross a major. Report the conflict verbatim.
   - npm: never regenerate `package-lock.json` yourself; it must be built on
     Linux or CI's `npm ci` breaks. Dependabot security updates are enabled
     and open the fix PR itself. If no PR exists for the alert, report it for
     a human.
4. **Applicable, but the fix needs a constraint change in `pyproject.toml` or
   `package.json`, or a major bump:** do not fix. Report for a human with the
   exact constraint that blocks it.
5. A package that is only in an extra CI never installs (`local-transcription`)
   is not exercised by the test suite. Say so in the PR body, and confirm the
   lock still resolves by running `uv lock --check` after the upgrade.

After merging a fix, re-query the alerts; they auto-close within a few
minutes. If one stays open, say so in the report.

## 4. Verify main after merges

After the last merge of the run, watch the `main` CI run it triggered:
`gh run list --branch main --limit 1 --json databaseId,status,conclusion`,
then `gh run watch <id> --exit-status`. A green PR that turns `main` red
(usually two independent bumps interacting) is reverted at once with
`gh pr create` from a `revert/...` branch, merged when green, and reported.

Merging to `main` also publishes a new prod image (`docker-publish` job), but
it does **not** deploy. Deploys are tag-triggered by a human. Note in the
report whether any merged bump touched a runtime dependency, so the human
knows a deploy would pick it up.

## 5. Digest: what's new that Thestill could use

For every Dependabot PR merged in the last week (including ones you merged
now), write a short entry:

- Read the release notes in the PR body. For minor and major bumps also fetch
  the upstream releases or changelog page for the full range.
- Find where Thestill uses the library: `grep -rn "import <pkg>\|from <pkg>"
  thestill/` for Python; `grep -rn "from '<pkg>" thestill/web/frontend/src`
  for npm.
- Write 1 to 3 lines: what changed that matters, which of our call sites it
  touches, and one concrete opportunity (a new API that would simplify
  existing code, a performance option, a feature that maps to something in
  `specs/`). Skip pure bug-fix releases with a single line "bug fixes only".

Publish the digest as a GitHub issue titled
`Dependency digest: week of <YYYY-MM-DD>` with label `dependency-digest`.
Group entries under headings "Worth a look" (real opportunities) and
"Routine" (bug fixes, no action). Put the sweep summary (section 6) at the
bottom of the same issue so the week has one record. If there is nothing to
report, do not open an issue.

## 6. Report

End with a summary that stands on its own:

- PRs merged (number, title, runtime or dev-only).
- PRs left open, each with the one-line reason.
- PRs closed, each with the reason.
- Alerts fixed / dismissed / deferred to a human, with reasons.
- `main` CI status after the run, and whether a runtime dependency changed
  (deploy would pick it up).
- Link to the digest issue.
- Anything that blocked you (missing tool, missing permission, API error),
  quoted verbatim.
