# Duty: code review

Watch my open pull requests (GitHub) and merge requests (GitLab), tell me in chat when
something changes, and show the whole list on request. One of the brahman's duties: see
`book/2-design.md` for where a duty sits.

## Abilities

| Ability | Trigger | Kind | Interaction |
|---|---|---|---|
| Watch list | my open PRs/MRs, found on every check; `/watch <url>` adds any other one | code | none |
| Merged / closed notice | scheduler (`schedule`, every 10 min) | code | notify |
| CI failed (or passed again), approved, changes requested | scheduler | code | notify |
| Merge conflict, needs a rebase | scheduler | code | notify, then ask **draft a rebase?** |
| Draft a rebase | an answer | code (`rebase.py`, plain git) | conflict: ask which side git keeps; clean: ask **push it?** |
| Push the draft | an answer | code | force-push with lease, only to `push_repos` |
| New comments by other people | scheduler | code | notify; *next: ask "auto-resolve?"* |
| `/update` | chat command | code | the MR table, after a fresh check |
| `/unwatch <url>` | chat command | code | stop watching, also for my own PRs |
| Morning digest | scheduler, Mon–Fri 09:00 | ReAct (`pr-review` skill) | notify; the `morning-prs` job |

When a PR conflicts or needs a rebase, the duty asks **draft a rebase?** On yes it fetches
the PR's head from the upstream repository (`pull/N/head`, `merge-requests/N/head`) and
rebases it onto its base in a scratch worktree (`state/rebase/<pr>`) of a local clone found
under `clone_roots`; none of my own worktrees is touched. Nothing is written by hand or by
a model: on a conflict it asks which side git keeps (mine = `-X theirs`, upstream's =
`-X ours`), or leaves the worktree mid-rebase for me. A clean draft asks **push it?** with
its commits and diffstat; yes force-pushes with `--force-with-lease` on the head the draft
started from, so a branch that moved meanwhile is never overwritten, and only to forks
listed in `push_repos`. For any other fork the answer is the push command, to run by hand.
GitLab hides a fork's path from anonymous calls, so it is guessed as
`<gitlab_user>/<project>` and the ask says so.

Not built yet: answering new comments (drafting a fix needs a ReAct step, and an upstream
policy check first).

## Steps (one check)

1. **List** what to watch: my open PRs on GitHub (`gh api graphql`, search
   `author:@me`), my open MRs in each GitLab project in `gitlab`, plus everything already
   being watched and everything added with `/watch`, minus `/unwatch`.
2. **Fetch** each one's state: open / merged / closed, CI, review decision, mergeability,
   and the comments and reviews by other people (not the author, not bots, not
   `ignore_authors`).
3. **Compare** with the last snapshot and turn every difference into a line.
4. **Post** the lines, grouped by PR, to `chat_id`, and only when there are any.
5. **Save** the new snapshot. A merged or closed PR is reported once and then dropped.

The first check only learns the current state and says how many PRs it watches; it
reports no changes, so turning the duty on doesn't flood the chat.

## Rules

- **Read-only.** The duty never comments, approves, merges or pushes.
- **A failing forge changes nothing.** If GitHub or a GitLab project can't be reached, its
  PRs keep their last snapshot and the notice ends with a ⚠️ line. An outage never looks
  like "merged" or "closed".
- **One writer.** The state file is locked, so a scheduled check and a chat `/update` never
  interleave.
- **No tokens.** GitHub goes through `gh`'s own login. GitLab uses the anonymous API:
  GraphQL for MRs and their notes, REST for head pipelines. Private projects aren't covered.

## Data

`$XDG_STATE_HOME/work_brahman/code-review.json` (by default
`~/.local/state/work_brahman/`):

| Field | What |
|---|---|
| `prs` | the last snapshot: key → state, CI, review, merge, comments |
| `manual` | keys added with `/watch` |
| `muted` | keys removed with `/unwatch` |
| `checked` | time of the last check |

A key is `github:owner/repo#12` or `gitlab:host/group/project!34`.
