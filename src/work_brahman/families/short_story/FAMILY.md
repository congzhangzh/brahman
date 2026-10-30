---
name: short-story
description: Work that waits on pipelines between its steps (wait on CI, do a step, wait on the next pipeline or build, do another, ..., done; a release is the typical one) reports its progress to the owner's chats as one card per story, edited in place. Use for "tell me when it's done", "report the progress", "track this", and whenever an agent starts such work.
---

# Family: short story

A story is one piece of work that waits on pipelines between its steps: wait on one
pipeline, do a step, wait on another, do the next, and so on until it is done. A release is
the typical one (CI green → push → wait on the build → publish → wait until it is live). The
waits are what the owner would otherwise keep asking about. The executor (a Claude Code
session, a script) does the work; the owner reads one card per story in chat (Lark, and
every linked chat) and does not have to ask "is it done yet?". It is short next to the stories of upstream-mr,
which last weeks: a short story lasts minutes to hours.

## Stages

| # | Stage | Leads | Executes | Where | Done when |
|---|---|---|---|---|---|
| 1 | **Start**: name the work and the marks expected | executor | `buddy short start TITLE --mark M …` → the story's number | chat: a new card | the card is in every linked chat; the number is told to the owner in the session |
| 2 | **Mark**: each milestone as it happens | executor | `buddy short mark N MARK [--status done\|doing\|failed\|skipped] [--note …]` | the same card, changed | the mark shows ✅ / ▶️ / ❌ / ⏭ with its time |
| 3 | **Replan**: more marks turn out to be needed | executor | `buddy short plan N MARK …`, or a mark nobody planned (it is added, marked *not planned*) | the same card | the card shows the new marks |
| 4 | **Note**: something worth knowing that is not a mark | executor | `buddy short note N TEXT` | the card's last lines | — |
| 3a | **Fork**: an if/else or a switch on the way | executor | plan it as a mark: `--mark "CI green? yes \| no=fix, rerun"` (2–4 options after `?`, split by `\|`; up to 3 steps after `=`); then `buddy short mark N "CI green?" --took no`, or mark a step under an option (that option is taken), or let the owner pick: `buddy short ask N --fork "CI green?"` | the card: the options under the fork as branches; the taken one with its steps, the others *not taken*; ◀ **here now** on the mark being done (else the next one to do) | the branch is taken; the main line goes on |
| 3b | **Loop**: the same step a few times, with an index | executor | plan it as a mark: `--mark "soak ×3"` (a count; `x3` too) or `--mark "build × linux, mac, win"` (one round per item), 2–9 rounds; then `buddy short mark N build [--status doing]` marks the current round, `--round K` a round by its index, `--status skipped` (no round) skips the rounds left | the card: the loop with ×N and the current round (`build ×3 · 2/3 · mac`), its rounds on one line under it, ◀ on the current round | every round settled: the loop is done (failed if a round failed) |
| 3c | **Open loop**: a retry or a poll, its count unknown ahead | executor | plan it as `--mark "rerun ×?"`; `buddy short mark N rerun --status failed --note …` ends a try and queues the next, `mark N rerun` is a try that worked, `--status doing` starts one more, `--status skipped` gives up | the card: `rerun ×? · try 3`, then one line per try with its time and note (the last 5), ◀ on the current try | a try worked (done), or given up; never more than 20 tries |
| 4a | **Ask**: a question only the owner can answer | executor | `buddy short ask N QUESTION [--option LABEL …] [--wait [--timeout S]]` → `question <id>` | the card: ❓ in its title, the question at the top in bold, one button per option | answered: `check` prints `answer <id>: <text>` once; `wait` exits 0 |
| 5 | **Steer**: stop the work, or tell the executor something | you | **■ Stop** or **💬 Reply** under the card; quote the card; `/short N <text>`, `/short N stop`; ■ STOP asks every running story | the card: "Stop asked", "replies not read yet" | the executor's next `buddy short` call prints it (exit 3 for a stop) |
| 6 | **Listen**: between steps and while waiting | executor | every `mark`/`plan`/`note`/`ask` prints new replies and answers; while waiting on a pipeline, `buddy short check N` every few minutes; on a question, `buddy short wait N [ID] --timeout S` at a safe point | the session | a reply or answer is acted on; a stop ends the work (stage 7, `--failed`) |
| 7 | **End** | executor | `buddy short done N` or `--failed --note why` | the card turns green or red; its buttons go | the owner has the result |
| 8 | **Read** | you | `/short`, `/short all`, `/short N`; the web workbench, level 1: this family is one workflow, each story one run (pick it: its marks in level 2, its card with 💬 Send and ■ Stop in level 3) | chat, web | — |

## Rules

- **The map keeps its shape; only ◀ moves.** A fork is planned with its options, so the
  owner sees from the start what may come and, at any time, where the work is. It stays
  small on purpose: 2–4 options, up to 3 steps under each, then back to the main line; a
  loop has 2–9 rounds, all planned, so its index says how far it is; an open loop (a retry)
  writes each try on its own line, so no try is written over by the next; nothing nests (no fork
  inside a fork or a loop, no loop inside a fork). Work that needs more is more than one
  short story.
- **Marks are a guide, not a contract.** They may come in any order; one that was never
  planned is added where it happens; the plan can grow (`plan`) while the work goes on. A
  mark can come before the work it names is finished (`--status doing`), and be marked again.
- **One card per story**, changed in place: no stream of notices. A card Lark refuses to
  patch (older than 14 days) is left as it is.
- **The number is the handle**: the session that started the story says it to the owner
  ("short story #12"), so either side can refer to it.
- **Only facts**: a mark says what happened (✅ CI green, ❌ login failed), never a guess;
  the session's own reply carries the reasons.
- **The owner is in the loop, not only watching it.** ■ Stop and 💬 Reply sit under a
  running card. The buddy cannot kill a session it did not start, so both are messages to
  the executor, kept in the story file: its next `buddy short` call prints each reply once,
  and exits with code 3 while a stop is asked. Exit 3 means: stop the work at a safe point
  (don't leave a half-pushed release), then `done N --failed --note "stopped by the owner"`.
- **A reply is an instruction from the owner**, as if typed into the session: act on it,
  and say on the card what changed (`note`, or a new mark).
- **Ask on the card, not in the terminal**, when only the owner can decide: he follows the
  work from his phone. `ask` puts the question at the top of the card with a button per
  `--option`; his answer is the first press or the first thing he writes (💬, quoting the
  card, `/short N <text>`). While a question is open, what he writes answers the oldest open
  one instead of going to the executor as a reply. An answer to a question already answered,
  or to a story that ended, is ignored. A story that ends with a question open shows it as
  not answered.
- **Answers are parseable**: `check`, `mark`, `plan`, `note` and `ask` print each new answer
  once as `answer <id>: <text>` (a newline in it written as `\n`). `wait N [ID]` blocks on one
  question (default the last asked) and exits **0** answered (it prints the answer again if a
  `check` already read it), **3** stop asked, **4** still waiting when `--timeout` runs out
  (`--timeout 0` looks once), **2** no such story or question, or it ended unanswered. It reads
  only the story file, every 2 s. Inside a tool call with a time limit, pass `--timeout`
  under that limit and wait again.
- Code only, no tokens: `buddy short` posts and edits cards itself; it does not start an agent.

## Where it lives

- The board: `$XDG_STATE_HOME/work_brahman/short_stories/<number>.json` (`src/work_brahman/short_stories.py`).
- From another directory or session: `scripts/buddy-short`, installed as `~/.local/bin/buddy-short`
  (loads the buddy's `.env` and `buddy.toml`).
