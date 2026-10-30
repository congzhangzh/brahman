---
name: pr-review
description: Review GitHub pull requests read-only and report findings. Use for "what needs my attention on my PRs" (overview of all open PRs) or to review one PR in depth.
---

# PR review (read-only)

Report on pull requests. Never post comments, approve, merge or push: those are
outward-facing and need a human.

Every `gh pr` command takes `--repo <owner/name>`, so this works from any directory.

## Overview mode: all my open PRs (default)

1. List them: `gh search prs --author @me --state open --json number,title,url,repository --limit 50`
2. For each PR, one call, no diff:
   `gh pr view <n> --repo <owner/name> --json title,reviewDecision,mergeable,statusCheckRollup,comments,reviews,updatedAt`
3. Report only PRs that need action, then a one-line count of the rest:

```
<repo>#<n> <title>
CI: pass | fail (<failing check>) | pending
Needs you: <unanswered review comments / changes requested / merge conflict>
```

"Needs you" is: a review comment or review newer than my last comment, changes
requested, failing CI, or `mergeable` = CONFLICTING. Nothing else.

## Deep mode: one PR

Use when given a PR number or URL.

1. `gh pr view <n> --repo <owner/name> --json title,body,baseRefName,headRefName,reviewDecision,comments,reviews`
2. `gh pr diff <n> --repo <owner/name>` and `gh pr checks <n> --repo <owner/name>`
3. Read the diff for correctness first, then risk: logic errors, missing error
   handling, behaviour changes the description does not mention, missing tests.
4. Report at most 3 findings, most severe first, each with `file:line`. Say
   "no findings" rather than inventing minor ones.
