---
name: upstream-mr
description: Take a change from real use to an upstream project and see it through review to the merge. One story per change; the PR/MR, its branches and its ledger entry are its record. Use for "upstream this", "send a PR", "where are my MRs", "whose turn is it on <pr>".
---

# Family: upstream MR

A **family** is an SOP we write once; a **story** is one instance of it, with its own
content. Here a story is one change for one upstream project: one topic, one branch, one
PR/MR. Distilled from the PRs and MRs done so far (FreeRDP, xrdp, gnome-remote-desktop,
SDL, cpython, ...); not designed up front.

Three parties take turns in a story: **you** (the owner, who leads and decides), the
**executor** (an agent session, local or in the cloud, or a buddy duty) and **upstream**
(the maintainers and their CI). At any time exactly one of them holds the turn.

## Stages

| # | Stage | Leads | Executes | Usually where | Done when |
|---|---|---|---|---|---|
| 1 | **Find**: a bug or a need, met in real use | you | — | anywhere | the problem fits in one sentence |
| 2 | **Prove**: reproduce, fix, test | you | executor | its own worktree on a `feature-*` or topic branch | a test or a measurement shows before ≠ after |
| 3 | **Policy**: may an agent's code go upstream? | you | executor reads the PR template, CONTRIBUTING, maintainers' word | — | *cleared* → 4; *refused or unknown* → ledger entry, stage 4 by your hand |
| 4 | **Prepare**: a clean branch on upstream's base, one topic | executor | executor | worktree `<repo>-<branch>`, role `mr` in `heads` | builds, tests pass, `privacy-scan` clean, message says what and why |
| 5 | **Submit** | you | you (ledger) or executor on your go | fork + upstream | the PR/MR exists; `heads` has `pr = N` |
| 6 | **Review loop**: CI, comments, rebases | upstream | executor drafts, you approve | the `code-review` duty watches | approved and mergeable |
| 7 | **Land**: merged or closed | upstream | executor | integration branches, ledger, kit pins | integration rebuilt without the carried copy; ledger entry `merged`/`dropped` |

## Whose turn (stage 6, read from the code-review snapshot)

| Signal | Turn | Next move |
|---|---|---|
| changes requested, or new comments by others | **you** | read them; say what to change (the executor drafts it) |
| conflict / needs a rebase | **executor → you** | the duty asks "draft a rebase?", then "push it?" |
| CI failed | **executor** | find out why (flaky, our fault, base broken); a fix comes back as a question |
| CI passed or none, no review yet | **upstream** | wait; ping only after a long silence, and only on your go |

## Your questions (always asked, never assumed)

- Is this worth sending upstream at all (stage 1→2)?
- Is the project cleared for agent-made code (stage 3)? Until you say so: the ledger.
- Submit, push, force-push, comment, close: every outward step, every time.
- Changes requested: accept, argue, or drop?

## May vary per story (say why in the story)

- A trivial fix may fold stages 2 and 4 into one branch.
- A stack (a feature in levels) has several stories that share stage 2's branch; each level
  is its own MR.
- A plain bug report, no code, may skip 4–6: facts, logs, a reproduction, filed on your go.

## Where a story's record lives

Nothing new: the PR/MR itself (public), the branch roles and `pr = N` in `<repo>/.git/heads`,
the ledger entry (`~/.claude/upstream-ledger.md`) and the code-review duty's snapshot
(`$XDG_STATE_HOME/work_brahman/code-review.json`), plus the threads duty's snapshot for who
spoke last on GitHub. `__init__.py` beside this file joins them into one row per story, with
its stage and whose turn it is: `/stories` in chat (`/stories all` for every row), `buddy
stories [--json]` in a terminal.

Not seen yet: your own replies on GitLab (nothing records them), and stories before stage 5,
except ledger entries.
