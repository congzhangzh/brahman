# Git branches by role (from brahman-buddy; `buddy setup` keeps it current)

Installed by `buddy setup` into `~/.claude/rules/`. Edit it freely: once you change it, setup
leaves it alone for good. `buddy setup --without brahman-git-roles` never installs it, and a line
in your own `CLAUDE.md` wins over anything here.

## Say where you are first

Before git work, and before and after anything that moves a branch (commit, rebase,
cherry-pick, merge, reset, push, a new branch or worktree), show a short card made from real
commands (`git worktree list`, `git branch -vv`, `git status --short`,
`git rev-list --left-right --count <base>...HEAD`), never from memory:

```
📍 <worktree path>   branch: <name>   role: <role>
   purpose : <one line: what this branch is for>
   HEAD    : <short sha> <subject>
   base    : <base> @ <short sha>  (ahead N / behind M)
   tracks  : <remote/branch, ahead/behind | none>
   state   : clean | N modified, M untracked | mid-rebase/merge
```

Mark a line that needs attention with ⚠️ (behind its base, unpushed, not clean, an action
that breaks the branch's role) and name the most important point in one line above the card.

## Roles

| Role | What it is | Key rule |
|---|---|---|
| **for_clean_mr** | One topic on the upstream branch, for a PR/MR | Only that topic's commits; rebase, don't merge |
| **feature** (`feature-<area>[-<level>]`) | An area's own long-lived history, maybe one level of a stack | Source of truth: a fix lands at the lowest level it belongs to, levels above are rebased onto it; reaches a PR only through a for_clean_mr branch |
| **for_integration** (`integration-<scope>`) | Several topics built and tested together | Never the source of a PR; rebuilt from the clean branches |
| **release** (`release-<YYYYMMDD>.<N>`) | A tested integration commit, picked and frozen, pinned by a consumer | Never commit to, move or delete it while anything pins it; a fix goes to its branch, the integration is rebuilt, then a new pick `.N+1` |
| **history_key** (e.g. `*-pre-rework`) | A bookmark of a milestone | Read-only: never commit to it or move it |

**The flow:** for_clean_mr / feature → for_integration (rebuilt) → release (picked when ripe)
→ pinned by the consumer, who ships what you chose, not the upstream schedule.

- **Pick a release** when an integration commit passed its tests: `git branch
  release-<YYYYMMDD>.<N> <sha>`, then push it. `<N>` counts that day's picks from 1. Say "pick",
  so it is not confused with `git cherry-pick`.
- **Everything may be rebased except a release.** A force-push to a branch behind an open PR
  rewrites what reviewers see: ask the owner first.
- **The base is a choice** (upstream's main, or a release tag for steadier ground); say it on
  the card, and ask when it is unclear.
- **A tag or a release is never moved.** A fix is the next number.
- When an action touches a branch, say its role and whether the action keeps the role's rule;
  when the role is unclear, ask instead of guessing.
