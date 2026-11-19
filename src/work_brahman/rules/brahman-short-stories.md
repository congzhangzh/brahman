# Short stories: report pipeline-bound work (from brahman-buddy; `buddy setup` keeps it current)

Installed by `buddy setup` into `~/.claude/rules/`. Edit it freely: once you change it, setup
leaves it alone for good; `buddy setup --without brahman-short-stories` never installs it.
Claude Code loads this file together with your own `CLAUDE.md`: they are combined, not ranked,
so where the two disagree Claude may follow either. To change what this says, edit this file
or leave it out, rather than contradicting it elsewhere.

A **short story** is work that waits on pipelines between its steps: CI green → push → wait on
the build → publish → live; a PR on CI, then review fixes; builds on several machines, then a
pick. The buddy keeps one card per story in the owner's chats (Telegram, Lark, the web page)
and edits it in place, so the owner never has to ask "is it done yet?".

**When:** at the first wait on a pipeline, a build, a deploy or a remote machine in work with
more than one step; for any multi-step work the owner would otherwise ask about (a review, a
multi-commit change, a recording); whenever they say "tell me", "notify me" or "track this".
Not for a single command, a quick answer, or a turn that ends within a minute or two.

```bash
n=$(buddy short start "Release 1.4" --who "<this session's name>" --mark "CI green" --mark build --mark live)
buddy short mark "$n" "CI green"                                   # a step happened
buddy short mark "$n" build --status doing --note "<run id>"        # a wait started
buddy short plan "$n" "rebase" "push again"                         # more steps turned up
buddy short note "$n" "waiting on the Mac"                          # a line that is not a step
buddy short check "$n"                                              # while waiting: replies? stop?
buddy short done "$n"                         # or: --failed --note "<why>"
```

- **Say the number** in your reply ("short story #12"). One story per piece of work.
- **Marks are a guide, not a contract:** plan the steps you expect, then mark what really
  happens. A wait starts as `--status doing` and ends as `done` or `failed`. A step can be a
  fork (`--mark "CI green? yes | no=fix, rerun"`, then `mark <n> "CI green?" --took no`), a loop
  (`--mark "build × linux, mac"`, then `mark <n> build --round 2`) or an open loop for retries
  (`--mark "rerun ×?"`: each `--status failed` opens the next try).
- **The owner steers from the card** (■ Stop, 💬 Reply): every `mark`, `plan`, `note` and
  `check` prints their new replies; treat each as their instruction, act on it, and `note` what
  changed. **Exit code 3 means they asked to stop:** stop at a safe point (never half a
  release), then `buddy short done <n> --failed --note "stopped by the owner"`, and say so in the
  session. A reply asking for something irreversible or outward-facing still needs their go in
  the session itself.
- **While waiting** on a pipeline, run `buddy short check <n>` at each poll. A line the buddy
  types into your window, or queues into a Codex session ("short story #12 has news…"), is a cue
  to run `check` (`buddy short attach <n>` makes a running story wake this session that way).
- **Only facts on the card** (✅ CI green, ❌ login failed, a run id, a SHA), never a guess.
  **Always end it** (`done`, or `--failed --note`), so the board never shows a dead story.
- **Private chats only.** A subagent never opens a story; the session that started it marks the
  subagent's results.
- **One story across sessions:** the coordinator starts, replans and ends it; the others mark
  their own steps. A step with waits of its own becomes a child story
  (`buddy short start … --parent <n>:<step>`), one level deep.

The stages, `ask` / `wait` and their exit codes are in the buddy's own notes:
`families/short_story/FAMILY.md` in the brahman-buddy package (its folder:
`python -c "import work_brahman, os; print(os.path.dirname(work_brahman.__file__))"`).
