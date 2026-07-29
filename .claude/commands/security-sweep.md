# Weekly Dependabot security sweep

Examine, fix, test, and close open Dependabot security alerts for ssarunic/thestill. Work autonomously; end with a concise summary report. Follow every guardrail below — the prime directive is: **never land a change that isn't proven by a green test suite.**

## 1. Preflight

- Must be on `main` with a clean tree; `git pull --ff-only`. If dirty or diverged, STOP and report — the live server runs from this checkout.
- Confirm `gh auth status` works.

## 2. Discover

- Open alerts: `gh api repos/ssarunic/thestill/dependabot/alerts --paginate` filtered to `state == "open"`.
- Open Dependabot PRs: `gh pr list --author "app/dependabot" --state open`.
- If there are no open alerts and no open **security** PRs: report "all clear" and stop. (Leave `chore(deps)` version-update PRs alone — they are not security work.)

## 3. Examine each open alert

Read the advisory (`security_advisory`, vulnerable range, patched version) and check whether the vulnerable code path is actually used in this codebase (grep for the affected API/mode/feature).

- **Not applicable** (vulnerable feature unused, e.g. an RSC-only or SSR-only advisory against our Vite SPA): dismiss via `gh api -X PATCH .../dependabot/alerts/<n> -f state=dismissed -f dismissed_reason=not_used -f dismissed_comment="<specific evidence>"`. The comment must cite what you checked.
- **Applicable, patch/minor fix** within existing version constraints: fix it (section 4).
- **Applicable, but fix requires a major version bump** or a constraint change in `pyproject.toml`/`package.json`: do NOT fix. Report it for a human decision.

## 4. Fix (conservative, lockfile-level only)

- **Python (`uv.lock`)**: `UV_PYTHON=/opt/homebrew/bin/python3.12 uv lock --upgrade-package <pkg>` (the UV_PYTHON pin is mandatory — other interpreters break sqlite-vec). Then sync the venv: `uv pip install -p ./venv/bin/python '<pkg>>=<patched>'`.
- **Frontend (npm)**: NEVER run `npm install`/`npm audit fix` or regenerate `package-lock.json` on macOS — it prunes Linux optional deps and breaks CI's `npm ci`. Instead use Dependabot's own security PR (Linux-generated lockfile): if its CI is green, merge it with `gh pr merge --squash`; if CI is red or no PR exists, report for manual handling.
- **Docker/Actions digests**: merge Dependabot's PR if CI is green.

## 5. Test gate (mandatory before any push)

- Run the full suite: `make test`. Any failure → `git restore`/`git reset` your changes, restore the venv package if you upgraded it, and report the failure with test output. Do not push. Do not retry with reduced scope.
- Known pre-existing failures: only proceed if you can show (e.g. via `git stash` + rerun) the failure exists without your change; note it in the report.

## 6. Land and verify

- Commit to `main` in the established style: `fix(deps): bump <pkg> <old> -> <new> (GHSA-xxxx)`. Push.
- Watch CI: `gh run list --branch main --limit 1` then `gh run watch <id> --exit-status`. If CI fails: `git revert` the commit, push the revert, report.
- Confirm the alert auto-closed (re-query alerts; allow a few minutes).

## 7. Server restart (only when warranted)

Only if a **runtime Python dependency** changed AND tests + CI are green:

- Find the process: `ps aux | grep 'thestill server'`. Send SIGTERM, wait up to 20 s for exit.
- Relaunch from repo root: `nohup ./venv/bin/thestill server > /tmp/thestill-server.log 2>&1 & disown`.
- Verify: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/` returns 200 and `/api/health` returns 200. If it doesn't come up, report loudly with the log tail.

Dev-only dependency changes (`transformers`, `typescript`, etc.) never justify a restart.

## 8. Report

End with a summary: alerts fixed / dismissed / deferred-to-human (with reasons), test results, CI status, whether the server was restarted. Then post a macOS notification:
`osascript -e 'display notification "<one-line outcome>" with title "thestill security sweep"'`
