# Short stories: report pipeline-bound work (from brahman-buddy; `buddy setup` keeps it current)

Installed by `buddy setup` into `~/.claude/rules/`. Edit it freely: once you change it, setup
leaves it alone for good. `buddy setup --without brahman-short-stories` never installs it, and a
line in your own `CLAUDE.md` wins over anything here.

A **short story** is work that waits on pipelines between its steps: CI green → push → wait on
the build → publish → live; a PR on CI, then review fixes; builds on several machines, then a
pick. The buddy keeps one card per story in the owner's chats (Telegram, Lark, the web page)
and edits it in place, so nobody has to ask "is it done yet?".

**When:** at the first wait on a pipeline, a build, a deploy or a remote machine in work with
more than one step; for any multi-step work the owner would otherwise ask about; whenever they
say "tell me", "notify me" or "track this". Not for a single command or a quick answer.

```bash
n=$(buddy short start "Release 1.4" --mark "CI green" --mark build --mark live)   # prints the number
buddy short mark "$n" "CI green"                                   # a step happened
buddy short mark "$n" build --status doing --note "<run id>"        # a wait started
buddy short plan "$n" "rebase" "push again"                         # more steps turned up
buddy short note "$n" "waiting on the Mac"                          # a line that is not a step
buddy short check "$n"                                              # while waiting: replies? stop?
buddy short done "$n"                         # or: --failed --note "<why>"
```

- **Say the number** in your reply ("short story #12"). One story per piece of work.
- **Marks are a guide, not a contract:** plan the steps you expect, then mark what really
  happens. A wait starts as `--status doing` and ends as `done` or `failed`.
- **The owner steers from the card** (■ Stop, 💬 Reply): every `mark`, `plan`, `note` and
  `check` prints their new replies; treat each as their instruction and `note` what changed.
  **Exit code 3 means they asked to stop:** stop at a safe point (never half a release), then
  `buddy short done <n> --failed --note "stopped by the owner"`. A reply asking for something
  irreversible or outward-facing still needs their go in the session itself.
- While waiting on a pipeline, run `buddy short check <n>` at each poll.
- **Only facts on the card** (✅ CI green, ❌ login failed, a run id, a SHA), never a guess.
- **Always end it** (`done`, or `--failed --note`), so the board never shows a dead story.
