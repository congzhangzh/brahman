# work_brahman

A small, extensible work buddy: talk to **Claude Code** from **Feishu / Lark** or
**Telegram** on your phone, run **scheduled jobs**, and grow it with **portable skills**.

**The guide** ([English](../book/README.md) · [中文](../book/README.zh-CN.md)): why
brahman exists, how it is designed, a real sample taken up the ladder, and a template to
copy for your own work.

```
 Feishu / Lark chat ──outbound WebSocket──▶ buddy ──▶ claude -p --resume <session>
 Telegram chat ─────outbound long polling──▶   │
 scheduler (in buddy serve) ───────────────▶ buddy job|duty <name> ──▶ result posted to a chat
                                                       │
                                     skills/ (SKILL.md) + MCP tools
```

Design rules:

- **Capabilities are portable files, the runtime is swappable.** Skills use the open
  `SKILL.md` format and tools use MCP, so every skill you write also works in Claude Code
  directly and in other agents that read the same format.
- **The agent owns the conversation.** The buddy stores only `chat_id → session_id`
  (in `~/.local/state/work_brahman/sessions.json`); history lives in Claude Code's own
  session store, so you can continue the same session in a terminal with
  `claude --resume <id>`.
- **No inbound ports.** Feishu's long connection and Telegram's long polling both dial out;
  the buddy needs no public IP, domain or tunnel, on a laptop or a cloud VM alike.
- **The agent never holds chat credentials.** `claude -p` only returns text; the buddy
  sends it. A chat app is one `Channel` class (`src/work_brahman/channel.py`).
- **Small.** About 6,500 lines of Python. Read it before you run it.

## Install as an app (ten minutes, Telegram)

For someone who wants the buddy, not its repository. You need Claude Code (installed and
logged in: run `claude` once), and either [uv](https://docs.astral.sh/uv/) with the wheel, or
the standalone `buddy` / `buddy.exe` (Python inside, nothing else to install). The service is
a systemd user unit on Linux and a `.cmd` in the Startup folder on Windows (no admin needed);
on macOS, run `buddy serve` in a terminal for now.

On Windows: typing into a terminal's long story from the phone needs `screen`, so that part
stays off; long stories started from the phone, and everything else, are the same code (long
stories are not tried on Windows yet). The
buddy runs in Python's UTF-8 mode there (it starts itself again with `-X utf8`), so files and
pipes are UTF-8 as on Linux.

```bash
uv tool install ./work_brahman-<version>-py3-none-any.whl   # the release you were given
buddy init                  # language, workspace, the token from @BotFather (/newbot)
buddy service install --enable
# write to your bot: it answers "Not paired" with a line to run, e.g.
buddy allow tg:123456789
systemctl --user restart buddy.service
buddy doctor                # ✅ what works, ⚠️ what is off and how to turn it on
buddy                       # every day: what needs fixing, the page, the stories running
buddy config                # later: change the language, chats, tokens, pairing, port
```

`buddy --help` ends with these steps; `buddy <command> --help` explains each option, and
`/help` in the chat says what you can type there.

The config is `~/.config/work_brahman/buddy.toml` (every setting: `buddy.example.toml`), the
secrets `~/.config/work_brahman/.env` (chmod 600). Parts whose tool is missing stay off:
duties need `gh`, long stories need `screen` or `codex`. Any of Telegram, Lark (international)
and Feishu (China) works, or all three at once: `buddy init` asks for each, and section 1
below says how to create a Lark or Feishu app. With both, the Lark app's ids read
`lark:oc_…` / `lark:ou_…`, so `buddy allow lark:ou_… --chat lark:oc_…` (the bot's reply says it).

## Quick start (from the repository)

Requirements: [uv](https://docs.astral.sh/uv/), Claude Code (logged in), and a Feishu or
Lark tenant where you can create an app, or a Telegram account, or both.

### 1. Create the Feishu app (once)

1. Feishu Open Platform → developer console → create a **self-built app** (企业自建应用).
2. Add the **Bot** capability.
3. Permissions: `im:message.p2p_msg:readonly`, `im:message:send_as_bot`, and
   `im:message.group_at_msg:readonly` if you want to use it in groups (@bot).
4. Events & callbacks → event config → **receive events through long connection**, add
   `im.message.receive_v1`. The console only saves this while a client is connected, so
   run step 3 below first, then save.
5. Create a version and publish it; make sure you are in the app's availability scope.
6. Copy the App ID and App Secret.

Telegram instead (or as well): message [@BotFather](https://t.me/BotFather), send
`/newbot`, and copy the token it gives you into `TELEGRAM_BOT_TOKEN`. Nothing else to set up:
Telegram is the quickest channel to try and to debug.

### 2. Configure

```bash
cp .env.example .env && chmod 600 .env     # App ID / Secret, bot token: here, nowhere else
cp buddy.example.toml buddy.toml           # workspace, allowed tools, jobs
scripts/link-skills.sh                     # make skills/ visible to Claude Code
```

### 3. Run and pair

```bash
uv run --env-file .env buddy serve
```

`serve` listens on every channel that has credentials in the environment. Send the bot
any message. While `BUDDY_ALLOWED_IDS` is empty it refuses everyone and replies with the
sender's id (`ou_…` on Lark, `tg:…` on Telegram); put yours in `.env` and restart. Only
listed users can drive the agent.

A "⏳ Working…" message appears at once and is replaced by the answer, with the time
taken underneath. On Lark the answer is a card, which renders Markdown including tables;
on Telegram it is converted to Telegram's HTML, with tables as aligned monospace blocks.

In chat: plain text goes to Claude Code (same session per chat), `/new` starts a fresh
session, `/help` lists commands.

**Topics: more than one conversation.** The chats of `[me]` share one main conversation,
the control panel. A **topic** is another conversation for a different piece of work, with
its own Claude Code session; its turns run beside the main one's, not queued behind them.
`/topic NAME` in any app (or **＋ Topic** in the web's Chat tab) opens one: a 🧵 card whose
**thread** holds it in Lark, and a **tab** on the web. Replying in a thread on any other Lark
message starts a topic too, named after its first words. What is said in a topic is
mirrored only to its thread and tab, never to the main chat; `/new` inside a topic resets
only that topic. On Telegram a topic is a **forum topic in the private chat with the bot**:
switch the bot's topic mode on in @BotFather (`getMe` then reports `has_topics_enabled`);
until then Telegram refuses ("the chat is not a forum") and its chat stays the main
conversation. A topic opened before that gets its Telegram topic at its next turn. Topics are kept in `$XDG_STATE_HOME/work_brahman/topics.json`
(`src/work_brahman/topics.py`), their sessions in `sessions.json` as `<chat>#<topic>`.

### 4. Schedule jobs and run in the background

Each `[jobs.<name>]` in `buddy.toml` has a prompt, a working directory, a chat to post to
and an optional `schedule`: cron (`0 9 * * 1-5`) or an interval (`every 10m`). The chat id
picks the channel: `oc_…` posts to Lark, `tg:<chat id>` to Telegram.

`buddy serve` schedules them itself, with no system timers, so it works the same on Linux,
macOS and Windows. Each run is a child process with a timeout; a run missed while the
buddy was down runs once when it starts; a task never overlaps itself. Every run, and
every chat turn, leaves a run record in `~/.local/state/work_brahman/runs/<day>/`: its
trigger, its steps with the agent session id, the result, and on failure the tail of
its output.

```bash
uv run --env-file .env buddy job morning-prs      # try a job by hand
deploy/install-units.sh                           # writes the systemd user unit
systemctl --user enable --now buddy.service
loginctl enable-linger "$USER"                    # keep running after logout
```

### 5. Duties

A duty is standing work with its own SOP: a package under `src/work_brahman/duties/<name>/`
holding the code and a `DUTY.md` that lists its abilities, steps and rules. Configure it
under `[duties.<name>]`. `buddy duty <name>` runs one check and posts only what changed;
`buddy serve` runs it on its `schedule`. A duty's chat commands join `/help`.

The first duty is **code-review**: it watches my open PRs (GitHub, through `gh`) and MRs
(any GitLab, anonymous API), and posts when one is merged or closed, when CI fails, when a
review comes in, when it needs a rebase, or when someone comments. `/update` checks now
and shows the table; `/watch <url>` and `/unwatch <url>` change the list.

```bash
uv run --env-file .env buddy duty code-review     # one check by hand
```

### 6. Families and stories

A duty repeats the same steps by itself. Much work is led by a person instead: the steps
are known, but every case differs, and the turn passes between the owner, an executor (an
agent session, local or in the cloud) and the outside world. A **family** is the SOP for
such work, written once: a package under `src/work_brahman/families/<name>/` with a
`FAMILY.md` (stages, who leads and executes each, the questions that are always the
owner's). A **story** is one instance of it.

The first family is **upstream-mr**: each PR/MR to an upstream project is a story. Its
stories come from what is already recorded (the code-review and threads snapshots, and the
upstream ledger), read-only. `/stories` shows whose turn each one is (`/stories all`: every
row).

```bash
uv run buddy stories            # the same, in a terminal; --json for scripts
```

The second family is **monthly-report**: one story per month. On the 1st at 09:00 (with
`[families.monthly-report] schedule`) the buddy posts what the month did upstream (merged,
opened, still open), a text Gantt chart of the work in flight, whose turn each open MR is,
and its own runs and cost. `/report` shows the month so far, `/report 2026-09` a past one.

```bash
uv run buddy family monthly-report --print   # print it here; without --print it posts
```

The third family is **short-story**: one story per piece of work that waits on pipelines
between its steps (wait on CI, do a step, wait on the next build, do another, ..., done; a
release is the typical one), short next to an upstream MR's weeks. The session
doing the work starts a short story, which gets a number and the marks it expects, and
reports each mark as it happens. The buddy keeps one card per story in every chat of `[me]`
and changes it in place (blue while it runs, green or red at the end). Marks are a guide, not
a contract: they can come in any order, a mark nobody planned is added where it happens, and
the plan can grow. `/short` lists the running stories, `/short all` every one, `/short 12` one
card.

Under a running card sit **■ Stop** and **💬 Reply**, so the owner can steer the work, not only
watch it. The buddy can't kill a session it didn't start, so both go into the story file
for the executor. Its next `buddy-short` call prints each reply once and exits with code 3
while a stop is asked; while it waits on a pipeline, it polls `buddy-short check 12`. A reply
can also be a quote of the card, or `/short 12 <text>` (`/short 12 stop`) from any chat, the
web included. ■ STOP asks every running story to stop.

In the web workbench (`[web] port`, http://127.0.0.1:8787) the family is one workflow in level
1 and each story is one of its runs: pick one, and level 2 draws its marks as the flow while
level 3 shows its card with 💬 Send and ■ Stop. A family shows there when it has `runs()`
(families/short_story).

The web **Chat** tab gets the card too, like everything posted to your chats: duties, jobs,
asks and short stories run in their own processes, which hand what they post to the web to
`buddy serve` through `$XDG_STATE_HOME/work_brahman/web-outbox/` (one file per change). Serve
applies the files in order every second and when it starts, so what is posted while it is
down shows up once it runs.
The Lark Base workbench (`[bitable]`) has a **Short stories** table, one row per story
(status, who, minutes, marks done of all, marks not planned, replies, where a stop came
from, the last event), and the family's row in **Workflows**. A table added to the code is
created in an existing Base on the next sync.

```bash
buddy-short start "Release 20261026.3" --mark "PR" --mark "kit build" --mark live   # → 12
buddy-short mark 12 "kit build" --note "web-20261026.3-…"   # --status doing|failed|skipped
buddy-short plan 12 "rerun tests"      # more marks expected
buddy-short note 12 "waiting for CI"   # a line that is not a mark
buddy-short check 12                   # replies not read yet; exit 3 if a stop was asked
buddy-short done 12                    # or --failed --note why
buddy-short list                       # the running stories; --all for every one
```

`scripts/buddy-short` runs `buddy short` from any directory with the buddy's `.env` and
`buddy.toml` (link it into `~/.local/bin`). The board lives in
`$XDG_STATE_HOME/work_brahman/short_stories/<number>.json`; the stages are in
`src/work_brahman/families/short_story/FAMILY.md`.

## Language and tests

The buddy speaks Chinese or English: `language` under `[buddy]` in `buddy.toml`, `"zh-CN"`
(the default, also when it is left out) or `"en"`. Every word it composes is in one catalog,
`src/work_brahman/text.py`, including the workbench page's, which `buddy serve` hands to the
page in both languages. The page starts in the configured one and has an EN / 中文 switch;
the choice is kept in that browser, and `?lang=en` or `?lang=zh-CN` in the URL picks one for
that load only (for screenshots).

```bash
uv run pytest      # no network, no token, no running buddy
```

`tests/scenes.py` drives the bot, asks, short stories, duties and families through fake
Lark, Telegram and web channels; `tests/golden_en.json` is what they said before the
catalog existed, so with `language = "en"` English stays byte for byte the same; a second
check renders every scene with the setting left out and expects the Chinese default. Rewrite
the golden file (`uv run python tests/scenes.py`) only when the English is changed on purpose.

## Adding a skill

Create `skills/<name>/SKILL.md` (frontmatter `name` + `description`, then instructions;
scripts next to it if needed), run `scripts/link-skills.sh`, and mention it in a message or
a job prompt. See `skills/pr-review/` for an example.

## Security

- Only paired ids are served; other senders are refused and logged.
- Headless Claude Code cannot ask for approval, so it may only use the tools listed in
  `buddy.toml` → `[claude] allowed_tools`. The default list is read-only. A blocked tool is
  named in the reply, so you can decide whether to allow it.
- Messages and replies pass through the chat provider's cloud (Telegram bot chats are not
  end-to-end encrypted). Do not use the buddy on repositories whose content must not leave
  your machine.
- The Telegram token is part of every Bot API URL, so the buddy never logs URLs.
- Secrets come from the environment only; `.env` and `buddy.toml` are git-ignored.

## Roadmap

- Approve/deny buttons on cards for tools outside the allow-list (needs card callbacks)
- One Feishu topic per session, `/sessions` overview
- Other engines (Codex) behind the same `Engine` interface; more channels (Slack, Matrix)
