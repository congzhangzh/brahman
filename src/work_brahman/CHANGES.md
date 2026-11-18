# What's new

The buddy shows this in the workbench's **What's new** tab, and posts the top section to your chats
once, at the first start of a new version (following `[notify]`).

## Unreleased

## 20261117.3

- **What a step changed and what it cost**: the detail panel shows a step's effects, by rules on
  the command run (git commit, push, force-push, merge, rebase, tag, `reset --hard`; gh and glab
  releases, PRs, merges and comments; systemctl; `rm -r`; docker and podman push, npm, uv and twine
  publish, `heads publish --yes`; story cards; an MCP tool by its verb). Names only, and an irreversible one is marked: ⚠ on its step in Plan, drawn apart in
  System. Your own rules go first, from `~/.config/brahman/effects.json`. The cost comes from the
  marking session's own transcript, between its "doing" and the mark that ends the step: calls,
  tokens in, out and cache, active time (a gap between two entries counts 5 minutes at most) and wall time.
  `$` shows only when `[views.prices]` in buddy.toml names the model's prices; the session's
  subagents are not counted, and the panel says so. Commands are read as the shell runs them: several
  lines, `sudo`, `timeout`, `git -C`, `gh -R` and quotes are understood. A rule of your own stores
  only its program unless it names its `"what"`, so a host or a path in your command never reaches
  the story.
- **Health check**: `GET /api/health` answers 200 while the buddy works (its heartbeat is fresh,
  every chat channel still listens, the scheduler runs) and 503 when a part fails, so `curl -f`
  alone tells. Without the web token it says only whether it works; with it, which part and the
  version too.
- **A link to one session**: `#session=3` opens the workbench on that long story (one Claude Code
  or Codex session), as `#story=12` and `#team=<kit>` open a story or a team; picking its tile
  writes it. With a web token set, the login page now keeps a link's `#…` part (it went to the plain
  page before).
- **Team roles**: `comp` is a **component developer**; "member" stays the word for any long-lived
  team session.

## 20261117.2

- **Three views of a story**: the workbench's Level 2 has Plan, System and Lanes on one shared
  selection: a step picked in any of them lights in all three, and one detail panel under them shows
  who did it and where, when, how long it waited for you, what it used and made (`--in`/`--out`),
  your tools and its note. Plan draws the steps in order (improvised ones dashed); System is the
  layout lit by the run (you on top, the input, the sessions, the effects, and under them skills, MCP
  servers, your CLIs, the web); Lanes gets a lane of your own on top: your answers, replies and
  ■ Stop as points, and how long each question waited for you.
- **Your tools on a step**: `buddy-short tools install` adds a Claude Code PostToolUse hook (a dated
  copy of settings.json first; `buddy-short tools uninstall` removes it). In a session that marks a
  running story it records, by rules, the skills, MCP servers, your own CLIs and the web the step
  used: names only, never the agent's built-in tools. Their arguments are copied apart from the
  story (readable by you only) and shown only behind ⚙ → 🐞 debug: arguments.
- **Links to the source**: a `--in`/`--out` value becomes a link only when its repo is certain: a
  URL; or `run`, `pr`, `mr`, `issue`, `sha`, `commit`, `release`, `tag` when the value names its repo
  (`owner/name#123`, `repo=owner/name`) or the checkout has exactly one GitHub or GitLab remote.
  Otherwise it stays plain text and the mark says how to name the repo. `--in` and `--out` are
  linked apart, and a value set again keeps no old link unless the new mark links it. Links show in
  the detail panel, on the cards and in the terminal, never in the public view.
- **Private or public, per story and team**: each short and long story is tagged when its text
  changes, by `brahman-privacy-mark` (your own on PATH or in `~/bin`, else the shipped sample: rules
  only; a missing, failing or slow marker means private). Teams are private until marked. Mark by
  hand with `buddy-short|buddy-long|buddy-team private <id> on|off`, or 🔒/🔓 on the page.
- **Public view** for a screen recording: the header's switch, for 3 hours in that browser, leaves out
  every private story and team, every duty run and question, the chat from before it and Setup, and
  sends no machine names, no links, and no URLs or repo names in a value's text; a red bar says so,
  and a line above the list says how many items it shows of how many there are. It never turns
  private by itself: when it ends, the page clears and asks.
- **⭐ Demo**: tag stories and teams for demos (`buddy-short|buddy-long|buddy-team demo <id> on`, or ⭐
  under ⚙), and the header's ☆ Demo shows only those; a private one stays hidden in the public view.
  A line above the list says when a filter hides items, with a ✕ to turn ☆ Demo off.
- **A quieter page**: the normal view keeps reading, your answers, ■ Stop and the reply boxes;
  ⚙ Advanced (kept per browser) adds the page's settings, Setup and each item's actions in one ⋯ menu.
- **After an outage**: a start more than 5 minutes after the last heartbeat posts one "I was down from
  … to …" notice (following `[notify]`), and each incoming message or press is logged, never its text.
- **Faster `buddy-short`**: a mark takes about 0.3 s instead of 3 to 8 s: Lark's SDK loads only when a
  Lark call is made, and a Telegram address family whose connect timed out is tried last for a week.
- **A Lark or Feishu reply** no longer fails with "'_thread._local' object has no attribute 'code'"
  when it is the first call of a new thread and the call worked.
- **The team rule** is installed by `buddy-team setup` as its own file, `~/.claude/rules/brahman-team.md`
  (Codex: a managed block in AGENTS.md, or nothing when a generator already copies the rules file).
  It now says that automatic work is script only, and has the edu role (one project, several or all).

## 20261113.2

- **The QR code is drawn** in the terminal by `buddy init` and `buddy scan`: `segno` is a dependency
  now (pure Python, nothing else pulled in), so no extra install is needed.

## 20261113.1

- **What's new**: this tab, and a post of the newest section to your chats at the first start of a
  new version (following `[notify]`, so with the defaults only this tab shows it).
- **Long stories**: in a story's thread, `/topys` brings up the pys window that holds the session or
  takes it over into a new one, `/frompys` gives it back to the buddy, and `/status` says who holds
  it; one name per move (the earlier `/shift`, `/shiftto`, `/shiftfrom`, `/takeover`, `/takeback` and
  `/handover` are gone: `/topys` hands over by itself). The workbench's
  long-story card has the same two buttons, **To pys** and **From pys**.

- **A long story from the web**: `/newlong` typed in the web chat now asks for the task there too;
  the question gets ↩ Reply, and the reply box answers the newest open question by itself, as
  Telegram does.
- **Every story's thread starts with your first question**, in every chat.
- **What's new** shows the running version and its commit, and the page draws Markdown headings
  and quotes.

## 20261112.7

- **Nothing on its own by default**: `[notify]` now covers every app (Telegram, the web chat, Feishu,
  Lark, web push), each `off` by default and followed live, no restart. Upgrading? Add
  `[notify] telegram = "all"` and `web = "all"` to keep the cards coming.
- **Real web push**, off by default: the workbench's 🔔 subscribes a browser (VAPID, a Service
  Worker, end-to-end encrypted); notifications even with no tab open; Setup → Ping tests it and lists
  the subscribed browsers. A manifest lets the workbench be pinned to a Home Screen (iOS web push).
- **The workbench from your LAN**, guarded: `BUDDY_WEB_TOKEN` (a login page, then a cookie) and
  `[web] hosts`, both applied without a restart.
- **zh-CN installs** don't offer Telegram or Lark (Setup shows them on request); doctor lists every
  outside endpoint in use (a fresh install: none).
- `/shift` and `/takeover` for long stories (renamed in the next release).
- Offline archives (buddy and pys together, installed with no network) are attached to this release.

## 20261112.6

- **Peers**: `allow` (local, private, public), resources by name (each machine keeps its own address),
  a self-description sent in the ping, the peers as a block in your global instructions (off by
  default); `buddy peer story`: one story on every machine.
- **Lanes** in level 2: who did what, where, in parallel (machine, session, subagent), with Attach to
  the session's pys window.
- **Feishu and Lark notify switch** (`[notify]`, off by default) and a count of message calls.
- `buddy scan feishu|lark`: an app by QR code on a buddy already set up, or an app you have, with a
  scan-to-enable link for its missing permissions.
- The `dbg` team role (tricky debugging across components).

## 20261112.5

- Thread replies a chat refuses go to the main chat instead, and doctor says why.

## 20261112.4

- A QR-scanned app writes to you first, so your chat is linked at once; unlinked chats can be linked
  from the Setup tab.

## 20261112.3

- `buddy service status`, `restart` and `uninstall`.

## 20261112.2

- A neutral example address in `buddy peer send --help`.
