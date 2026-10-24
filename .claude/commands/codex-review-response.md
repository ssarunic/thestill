# Codex review response

Answer every Codex review finding on the open pull requests of
`ssarunic/thestill`. For each one, check the claim against the code, then
either fix it on the PR branch or reply with why not. Every finding gets an
answer in its own review thread, and each PR you touch gets one summary
comment. Work autonomously and end with a summary report.

This runbook is run by a scheduled Claude cloud routine (hourly). It can also
be run by hand as `/codex-review-response` from a local Claude Code session,
optionally with a PR number (`/codex-review-response 419`) to handle only that
PR.

Prime directive: **a finding is a claim, not an instruction.** Adopt it only
when you have shown it is true against the current code. Reject it with a
reason a reviewer can check. Merge only under §6's conditions, never push to
`main`, never force-push.

## 0. Tooling and access

- GitHub access works as in `dependency-sweep.md` §0: the `gh` CLI when
  `gh auth status` passes, else the GitHub MCP tools (`mcp__github__*`, loaded
  with `ToolSearch`), else `curl` against `https://api.github.com` with
  `GH_TOKEN` as a bearer token. The examples below use `gh api`; every one has
  an equivalent in the other two forms. If none works, STOP and report the
  failing call verbatim.
- The cloud checkout starts on `main`. Do not run `git stash`, `git reset` or
  `git add -A`; stage named files only. Local sessions use the same branches,
  so another session may be working on a PR branch at the same time.
- Python: `uv venv && uv pip install -e ".[dev]"` (install `uv` with
  `curl -LsSf https://astral.sh/uv/install.sh | sh` if missing), then
  `.venv/bin/python -m pytest <files>`. Worktrees and fresh checkouts need
  `PYTHONPATH=$PWD`. Frontend: `cd thestill/web/frontend && npm ci`, then
  `npx vitest run <files>` and `npx tsc --noEmit`. Never run `npm install` or
  regenerate `package-lock.json`. If setup fails, say so in the reply and rely
  on CI.
- Follow `CLAUDE.md` and `docs/code-guidelines.md`: match the surrounding
  code, use structured logging, and never change data with raw SQL.

## 1. Discover unanswered findings

The Codex reviewer is the bot `chatgpt-codex-connector[bot]`.

1. Open PRs: `gh pr list --state open --limit 500 --json
   number,title,headRefName,headRepositoryOwner,isDraft,author` (without
   `--limit`, `gh` stops at 30). Skip drafts,
   Dependabot PRs (`app/dependabot`), and PRs whose head repository is not
   `ssarunic` (forks: you cannot push).
2. For each PR, list the review comments:
   `gh api repos/ssarunic/thestill/pulls/<n>/comments --paginate`.
   - A **finding** is a top-level comment (`in_reply_to_id` is null) by the
     Codex bot. Its body starts with a priority badge (`P0`–`P3`) and a
     one-line title.
   - A finding is **answered** when its thread has any reply (a comment whose
     `in_reply_to_id` is the finding's `id`) by a login other than the Codex
     bot. Skip answered findings.
3. Codex can also put findings in a review body with no inline comments
   (`gh api repos/ssarunic/thestill/pulls/<n>/reviews --paginate`, author is
   the bot,
   `body` contains a `P0`–`P3` badge). Treat that body as one finding. It is
   answered when a PR comment contains `<!-- codex-response review=<id> -->`.
4. Ignore the bot's issue comments (the "Codex Review Summary" status table,
   a "### Summary" of a Codex task, the "connect your account" notice) and
   its 👀 reactions. None of them is a review.
5. Codex does not always start on its own. For a PR with no review by the
   bot, no 👍 from it, and no `@codex review` comment **by a login other than
   the bot** (its own summary comment quotes the phrase), post
   `gh pr comment <n> --body "@codex review"`, once per PR. If the bot's only
   answer to the last trigger was a refusal ("reached your Codex usage
   limits", "connect your account"), post it again, at most once every 24
   hours. Findings arrive within about 15 minutes, so the next run answers
   them.

If no PR has an unanswered finding, skip to §6.

## 2. Judge each finding

Work on each PR in its own temporary worktree, never in the main checkout:
a local run shares that checkout with other sessions, and switching or
resetting its branch would throw away their work.
`git fetch origin <branch> && d=$(mktemp -d) && git worktree add --detach
"$d" origin/<branch>`, then work in `$d` and remove it with
`git worktree remove "$d"` when the PR is done. Read the finding, the lines it points at
(`path`, `line`, `commit_id`), and enough of the code around them to judge it.
The finding may be against an older commit; judge it against the branch head.

Decide exactly one outcome:

- **Adopt** when the bug or gap is real at the head commit and fixing it
  belongs in this PR. You have shown it is real when a test reproduces it, or,
  where no test can, when you can trace the failing path through the code
  and name it.
- **Already fixed** when a later commit on the branch has already dealt with
  it. Name that commit.
- **Reject** when the claim is false (cite the code that disproves it), when
  the behaviour is deliberate (cite the comment, spec or test that says so),
  or when the cost of the fix outweighs a risk you can state concretely.
- **Needs a human** when the fix would change an LLM prompt (prompt changes
  need the before/after episode check, which a cloud run cannot do), a
  database migration, auth or permissions, public URLs, or anything that
  needs production data to judge. Also use it when the finding is real but
  out of this PR's scope. Say what a person has to decide.

A P3 or style finding is adopted under the same test. Being cheap to fix is no
reason to adopt it.

## 3. Fix the adopted findings

For each adopted finding, on the PR branch:

1. Write a test that fails without the fix, next to the PR's existing tests,
   when the behaviour can be tested. Run it and see it fail.
2. Make the smallest change that fixes it, in the style of the surrounding
   code.
3. Run the test files touching the changed code. All must pass.
4. Commit the named files with a message that says what was wrong and why
   (`fix(<area>): <what>` plus a short body). One commit per finding, or one
   for several findings that share a fix. No attribution lines.

Then push: `git push origin HEAD:<branch>` (the worktree is detached). If
the push is rejected because the branch moved, fetch, rebase **only your own
new commits** onto `origin/<branch>`, rerun the tests and push once more. If that fails too,
do not push. Reply on the findings that the fix is ready but the branch kept
moving, and include the diff in the reply.

When this run adopted a P0, P1 or P2 finding and pushed its fix, ask Codex
to review the fix with one PR comment after the push:

```text
@codex review

<!-- codex-rereview -->
```

Do not trigger Codex after fixing only P3 findings. A PR gets at most
**three re-review rounds**: count the PR comments that contain
`<!-- codex-rereview -->`. The §1 step 5 trigger and any `@codex review` a
person posts by hand do not count. The cap stops a fix → finding → fix loop,
so once a PR has used its three rounds:

- do not trigger Codex on it again;
- do not push fixes for new findings on it either. Judge and answer them as
  usual, but mark a finding you would adopt **Needs a human** and put the
  proposed diff in the reply;
- say in the PR comment that the PR has used its re-review rounds and is
  left for a person.

Apart from re-review rounds, §1 step 5 is the only time you trigger Codex.

## 4. Answer

- Reply in each finding's thread:
  `gh api repos/ssarunic/thestill/pulls/<n>/comments/<finding id>/replies
  -f body=...`. Start with the outcome in bold (**Adopted**, **Already
  fixed**, **Rejected**, **Needs a human**). Then give one to three sentences
  of evidence, plus the commit SHA when you pushed. Never make a reply a bare
  "fixed".
- Post one PR comment summarising the run: what was adopted and pushed (with
  SHAs and tests added), what was rejected and why, what needs a human. For a
  review-body finding, include `<!-- codex-response review=<id> -->` in this
  comment.

## 5. Watch CI on what you pushed

Wait for the PR's checks on your new head (`gh pr checks <n> --watch`, up to
25 minutes).

- Green: done.
- Red because of your commit: fix it and push again, at most twice. If it is
  still red, revert every commit you made in this run, newest first, with
  `git revert` (never a force-push and never someone else's commit), push,
  and say so in a follow-up PR comment.
- Red for a reason that is also red on `main` or on the PR's previous head:
  not yours. Note it in the PR comment and leave it.

## 6. Merge PRs that passed review clean

The owner's standing rule (2026-10-23, amended 2026-10-24): a PR is merged
into `main` once Codex has reviewed it, it has no P0 or P1 finding, every P2
finding was fixed and Codex has reviewed the fix clean, and CI is green.
Check every open PR from §1's list (not only the ones you touched), and merge
it with `gh pr merge <n> --merge --match-head-commit <head sha>` when **all**
of these hold:

- Authored by `ssarunic`, not a draft, no `hold` label, and no reviewer has
  requested changes.
- Codex has reviewed it: a review by the Codex bot, or its 👍 reaction on the
  PR (`gh api repos/ssarunic/thestill/issues/<n>/reactions --paginate`). The bot's
  "Codex Review Summary" status comment alone is not a review.
- No finding on the PR, at any commit, carries a P0 or P1 badge. A PR that
  had one waits for a person even after the fix. Say so in the report.
- Every P2 finding is answered **Adopted** or **Already fixed**, and Codex
  has reviewed the fix clean: its 👍 reaction on the PR is newer than the
  latest `<!-- codex-rereview -->` comment, that comment is newer than the
  last push of a code change (a §6 branch update that only merges `main`
  does not count), and no finding has arrived since. A P2 answered
  **Rejected** or **Needs a human**, or a fix Codex has not re-reviewed, waits
  for a person. Say so in the report.
- Every P3 finding has been answered (§4).
- CI on the head commit is fully green: no failed, cancelled or pending
  check.
- `gh pr view <n> --json mergeable,mergeStateStatus` shows `MERGEABLE` and
  `CLEAN`. If it is `BEHIND`, update the branch with
  `gh api -X PUT repos/ssarunic/thestill/pulls/<n>/update-branch` (a merge
  commit, never a rebase or force-push), wait for CI on the new head, and
  check again. If it conflicts, leave it open and report it.

After merging, delete the remote branch only when the PR was opened from a
branch no local worktree uses. If unsure, leave it.

## 7. Report

End with a summary that stands on its own:

- Per PR: the findings, each with its outcome and one-line reason, plus the
  SHAs pushed and the CI result.
- PRs merged (number, title, merge SHA), and PRs that were otherwise ready
  but left open, with the condition from §6 they failed.
- Anything that blocked you (a missing tool or permission, an API error, a
  push rejected twice), quoted verbatim.
- If anything was pushed or needs a human, send a push notification naming
  the PRs. Otherwise send none.
