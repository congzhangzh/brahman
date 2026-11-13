# work_brahman

A small, extensible work buddy: talk to **Claude Code** from **Feishu / Lark** or
**Telegram** on your phone, run **scheduled jobs**, and grow it with **portable skills**.

![The buddy in a terminal: install, init, doctor, then a short story started, marked and listed](https://github.com/congzhangzh/brahman/releases/latest/download/buddy-demo.gif)

Recorded for every release, in a clean container as a made-up user ([the same as a video](https://github.com/congzhangzh/brahman/releases/latest/download/buddy-demo.mp4)).

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
uv tool install brahman-buddy             # from PyPI (or ./brahman_buddy-<version>-py3-none-any.whl)
buddy init                  # language, workspace, the token from @BotFather (/newbot)
buddy service install --enable
# write to your bot: it answers "Not paired" with a line to run, e.g.
buddy allow tg:123456789
systemctl --user restart buddy.service
buddy doctor                # ✅ what works, ⚠️ what is off and how to turn it on
buddy                       # every day: what needs fixing, the page, the stories running
buddy tui                   # doctor, config, peers and the peer log in one screen
buddy config                # later: change the language, chats, tokens, pairing, port
buddy service status        # running? since when, its pid, where the log is (exit 3: not running)
buddy service restart       # after an upgrade or a config change
buddy service uninstall     # stop it starting by itself (the unit/plist is kept as a backup)
```

**One screen for the setup.** `buddy tui` has four views, Tab between them: **Doctor** (the
quick checks at once, `r` every check, each ❌ with its fix), **Config** (what `buddy config`
changes; secrets shown as their last four characters only), **Peers** and **Log** (section
"Two brahmans" below). Nothing is written until `w`, and then with a backup; a config change
counts after a restart of the buddy, which the TUI says and never does itself. The workbench's
**Setup** tab is the same, through the same code (`settings.py`), and never receives a secret:
it sends a new one, or `-` to remove one. For scripts and cron, `buddy doctor` stays: exit 1 on
any ❌, `--json` for the rows, `--peers` to add the peer checks.

**One buddy at a time.** `buddy serve` holds a lock in its state folder
(`~/.local/state/work_brahman/serve.lock`, its pid beside it): a second one, typed by hand while
`buddy.service` runs, stops at once with exit code 3 instead of fighting the first for the
chats. From a checkout, `scripts/buddy` runs `buddy` from any directory with that checkout's
`.env` and `buddy.toml`, as the service does (`ln -s "$PWD/scripts/buddy" ~/.local/bin/`).

`buddy --help` ends with these steps; `buddy <command> --help` explains each option, and
`/help` in the chat says what you can type there.

The config is `~/.config/work_brahman/buddy.toml` (every setting: `buddy.example.toml`), the
secrets `~/.config/work_brahman/.env` (chmod 600). Parts whose tool is missing stay off:
duties need `gh`, long stories need `screen` or `codex`. Any of Telegram, Lark (international)
and Feishu (China) works, or all three at once: `buddy init` asks for each, and section 1
below says how to create a Lark or Feishu app. With both, the Lark app's ids read
`lark:oc_…` / `lark:ou_…`, so `buddy allow lark:ou_… --chat lark:oc_…` (the bot's reply says it).

**The workbench from another computer.** The page listens on `127.0.0.1:8787` only (`[web]
port`), so reach it through ssh. On the other computer:

```bash
ssh -N -L 8787:127.0.0.1:8787 you@server      # then open http://localhost:8787/
ssh -N -L 18787:127.0.0.1:8787 you@server     # 8787 taken there: any local port works
```

Log in as the user the buddy runs as: on Linux the page asks the kernel who owns each
connection and refuses anyone else, and through a tunnel that is your ssh login. The address
must be `localhost` or `127.0.0.1` (the page checks the `Host` header), not the computer's
name. Keep the forward on that computer's loopback: no `-g` and no `0.0.0.0:`, since the page
acts as you. To reuse it, put this in that computer's `~/.ssh/config` and run
`ssh -fN buddy-wb`:

```
Host buddy-wb
    HostName server
    User you
    LocalForward 8787 127.0.0.1:8787
    ExitOnForwardFailure yes
    ServerAliveInterval 30
```

## Quick start (from the repository)

Requirements: [uv](https://docs.astral.sh/uv/), Claude Code (logged in), and a Feishu or
Lark tenant where you can create an app, or a Telegram account, or both.

### 1. Create the Feishu app (once)

**Or let a QR code make it** (optional): `buddy init --feishu-scan` (or `--lark-scan`, or type
`scan` at the App ID question) shows a QR code; scan it in Feishu or Lark and confirm, and the app
comes back with its App ID and Secret, the scopes below asked for, and you paired. A Lark tenant
is found by itself. `uv pip install segno` draws the QR code in the terminal; without it you get
the link. Check it afterwards with `buddy doctor`: whether every scope is granted at once and the
events come on the long connection is the platform's to say. The steps below are the manual way.
On a buddy that is set up already, **`buddy scan feishu`** (or `lark`) does the same in one step:
the QR code, the app written into `.env` (with a backup), you paired and your chat linked (the new
app writes to you first), doctor's rows for it, then the restart (asked, or `--restart`).
Replacing an app that is there is asked first (`--yes` skips that). In `buddy tui`: Config → `S`.
It asks first, as the official `lark-cli config init` does: a new app, the one the buddy uses now
(`--current`), or another you have (`--app ID`, its Secret asked hidden). For an app that exists
it checks the scopes and shows a QR code of the scan-to-enable link: the developer console opens
on that app with only what the buddy needs (missing scopes, the message event, the card
callback) to turn on; Enter checks again. `buddy doctor` points to it when a scope is missing.

1. Feishu Open Platform → developer console → create a **self-built app** (企业自建应用).
2. Add the **Bot** capability.
3. Permissions: `im:message.p2p_msg:readonly`, `im:message:send_as_bot`, and
   `im:message.group_at_msg:readonly` if you want to use it in groups (@bot).
4. Events & callbacks → event config → **receive events through long connection**, add
   `im.message.receive_v1`. The console only saves this while a client is connected, so
   run step 3 below first, then save.
   Then **callback config** (回调配置) → long connection too, add **卡片回传交互**
   (`card.action.trigger`): without it the buttons on the buddy's cards (asks, ■ Stop) show an
   error and nothing reaches the buddy. No API shows this setting, so `buddy doctor` can't read
   it: send a ping from the workbench's Setup tab → Ping and press **🏓 Pong** on the card; the
   doctor then shows "card buttons work".
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

**Nothing is sent on its own until you say so.** `[notify]` in buddy.toml says, per app, what the
buddy sends without being asked: story cards, duty results, mirrors of what you said elsewhere.
The default for every app is `off`: it answers you, nothing else.

```toml
[notify]
telegram = "all"   # off (default) · ends: a story's start and end · all: every card change
feishu = "ends"    # Feishu and Lark count every message call against a monthly quota (99991403)
lark = "off"
web = "all"        # the workbench's chat
webpush = "off"    # your browsers, even with no tab open (🔔)
```

A chat you wrote or pressed in within ten minutes still gets the posts and questions that follow
from it. It is followed live: a change applies to the next message, no restart. `buddy doctor` shows
each app's setting, the message calls this buddy made this month (Feishu, Lark), and every outside
endpoint in use (a fresh install calls none); `buddy tui` → Config and the Setup tab change it.
A zh-CN setup doesn't offer Telegram or Lark (`buddy init` doesn't ask; Setup shows them on request).

`[notify]` is followed live: the running buddy reads it again when buddy.toml changes, so a switch
changed in the Setup tab, the TUI or by hand applies to the next message, with no restart (other
settings still need one).

**The workbench from another device on your LAN** (an SSH forward with a wildcard bind, e.g.
`ssh -L 0.0.0.0:18787:127.0.0.1:8787 server` on a machine the phone can reach): set a long random
`BUDDY_WEB_TOKEN` in `.env` (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) and
list the addresses the browser will use in `[web] hosts` (e.g. `["192.168.1.10"]` or
`["192.168.0.0/16"]`), then restart. Every request then needs the token: open
`http://192.168.1.10:18787/?token=…` once (it becomes a cookie and leaves the address bar), or type it
on the login page; scripts send `Authorization: Bearer …`. Without a token, `[web] hosts` is ignored.
Plain HTTP: anyone on that network can read the token and the pages, and web push needs
127.0.0.1 or HTTPS; an SSH client on the device itself (`-L` to 127.0.0.1) has neither problem.

**A long story into a terminal, from the phone:** in its thread, `/takeover` opens a pys window on
the buddy's machine, in the story's folder, running `buddy-long take N` (it waits for the buddy's turn
to end); `/shift` brings up the pys window that holds it already. The story's first message lists
them with `/status`, `/handover` and `/takeback`. The workbench's Resume and Attach do the same.

**Notifications outside the chats.** In the workbench, 🔔 (top right):

- by default, system notifications **from that tab** (a question, a story that ends or fails, while
  the tab is behind); nothing leaves the machine;
- with `[notify] webpush = "ends"` (or `"all"`) in buddy.toml and a restart, 🔔 **subscribes that
  browser to real web push**: notifications come even with no tab open. Nothing to register
  anywhere: the buddy makes its own VAPID key; each browser makes its own keys, and every message is
  end-to-end encrypted for it (RFC 8291), so the browser maker's push service (Google's, Mozilla's,
  Apple's) only carries it. Each browser you want subscribes once; click again to unsubscribe.
  `buddy push test` sends one to every subscribed browser. Off by default.

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
while a stop is asked; while it waits on a pipeline, it polls `buddy-short check 12`. If it is a
Claude Code session in a pys or screen window, the buddy also types a fixed line there telling it
to run `check` (`buddy-short attach 12` for a story started earlier). A reply
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

## Teams

A **team** is the who of the work: one per kit, its release manager (RM), its product owner
(PO) and one long-lived member per area, each carried by one generation of sessions at a time.
One live card per kit shows a row per role: the session, its pys window, its age (years =
compactions, and ctx now, ⚠️ from `age_warn`), its book, its running short stories, and the
last handovers. The RM records what it did; the card looks at the rest and changes only when
the banded facts do. 💬 Reply under it goes to the RM.

```bash
buddy-team new app-release-kit --rm app-rm app-release-kit-1 --book role-release-manager.md
buddy-team join app-release-kit server-a1 --role server --area "the server, its daemon" --project ~/projects/server
buddy-team handover server server-a1 server-g2     # the same for the RM's own role
buddy-team show app-release-kit               # exact ctx, now; check: the owner's replies
```

The stages and rules are in `src/work_brahman/families/team/FAMILY.md`; the idea in
`specs/team.md`.

## Two brahmans passing bundles (`buddy peer`, restricted environments only)

> ⚠️ **One shared secret and plain HTTP.** Anyone with the secret can send as any peer, and
> anyone on the path can read what is sent. Use it only on a LAN you own, a VPN (WireGuard,
> Tailscale) or an SSH tunnel, never on the open internet. It says so each time it runs.

Both sides set the same `BRAHMAN_PEER_SECRET` (32 characters or more). The receiver runs
`buddy peer serve` (127.0.0.1:8790 unless `--bind` names a LAN, VPN or tunnel address); the
sender runs `buddy peer send http://<peer>:8790 <repo> "main..my-fix" --note "what, where to
look"`. A signed bundle (HMAC-SHA256 over time, sender and the body's hash; 5 minutes' clock
skew; no replay) is only stored, under `~/.local/state/work_brahman/peer-inbox/`; nothing is
applied or run. The receiving session looks with `buddy peer inbox`, then `git bundle verify`
and `git fetch <bundle> my-fix:from-peer`, and runs its own tests, as with any proposal. Shared
context (a file share, GitHub) is named in the note. The full pairing (keys per peer, trust
scopes, a gate) is still a design: `specs/multi-brahman.md`.

The peers live in `~/.config/work_brahman/peers.toml` (this side's name, where serve listens,
each peer's address and a note; never the secret), changed in `buddy tui`'s **Peers** view or the
workbench's **Setup** tab (nothing written until you write it, with a backup). Both run the same
**doctor** as `buddy peer doctor` (and `buddy doctor --peers`): the secret, where serve listens,
and a signed ping to every peer, answered signed, so it proves the same secret on both sides and
shows how far apart the clocks are. For the workbench to check peers, put `BRAHMAN_PEER_SECRET`
in the buddy's `.env`. `buddy peer send <peer name> …` takes a name from the file. `buddy peer
log` (and the Log views) shows what came, what was refused and why, what was sent, and every
check.

**One story on every machine** (`specs/multi-brahman.md`): `buddy peer story start "the job" --step a1
--step b1@mac --step a2` starts a story here (its card in your chats) and sends the plan to every peer;
every machine keeps the whole story. Each step has one owner machine, and only it marks the step:
`buddy peer story mark <machine>#<n> b1 --status done --note "…"` on the Mac moves the card here. `buddy
peer story show <id>` and `list` show it as this machine has it; `buddy peer story sync` catches up both
ways after a machine was away. A peer that can reach you but you can't reach is send-only
(`direction = "in"` in peers.toml): it pushes and pulls, and doctor shows when it was last heard from.
Events only mark, note and plan steps; nothing a peer sends is run.

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
- The web workbench binds `127.0.0.1` only and, on Linux, answers only connections owned by
  the buddy's own user; from another computer, use an `ssh -L` tunnel (see "Install as an
  app").

## Roadmap

- Approve/deny buttons on cards for tools outside the allow-list (needs card callbacks)
- One Feishu topic per session, `/sessions` overview
- Other engines (Codex) behind the same `Engine` interface; more channels (Slack, Matrix)

## License

MIT: see LICENSE. Provided "as is", without warranty of any kind.
