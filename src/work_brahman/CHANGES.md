# What's new

The buddy shows this in the workbench's **What's new** tab, and posts the top section to your chats
once, at the first start of a new version (following `[notify]`).

## Unreleased

## 20261126.1

- **A limited network is fine** (the owner, 2026-11-26: "hung and no serve", "buddy doctor itself hung!").
  `buddy serve` starts its web page first; the start-time notes (what's new, was down) go out beside it; every
  Lark/Feishu call has a timeout (the SDK had none), and a send that doesn't get through now simply fails (the
  card or post is not sent, nothing hangs or breaks; it is said once, as below). An app that can't be reached is said once, on every page
  and in `buddy doctor` ("Lark can't be reached since 10:02: the web page, the scheduler and the duties work"),
  and again when it is back ("Lark reached again at 10:14"). `buddy doctor` shows the local checks at once and
  gives the network checks 10 seconds together; one that doesn't answer is "not reached". Tested on Linux; not yet
  run on Windows.

- **Native push to your own apps through Apple (APNs)** (the owner, 2026-11-25: native push). Off by default:
  `[notify] apns = "ends"` or `"all"`, plus Apple's push key: the .p8 at `~/.config/brahman/apns.p8` (chmod 600)
  and `[apns] key_id`, `team_id`; without them it stays off and `buddy doctor` says what is missing. An app
  registers its device (POST `/api/push/apns`, behind the owner login); each event goes once to each device,
  with only its title (or `[notify] apns_text = "none"`: "brahman: news on #N"), the same choice and event id as
  web push; a token Apple no longer knows is dropped.

- **Each paired device has its own secret** beside the owner secret (the owner, 2026-11-25: one setup per
  device). A device makes its secret at its setup and leaves only its SHA-256 in
  `~/.config/brahman/owner.d/<device>.sha256`. The workbench and the assistant accept the owner secret or any
  device's, compared in constant time; deleting a device's file logs out that device and no other. A file
  that isn't one hex line, or a link, opens nothing. `buddy doctor` counts the devices and names a bad file.

- **The login forms name the owner secret**: while `owner.secret` is set up, the workbench's and the
  assistant's login forms ask for the owner secret, no longer a "web token" (without it, as before). A login
  from the shell's own port gets in too.

- **The quick start's fix step: `buddy setup --remote <verb> [<space>]`** (the owner, 2026-11-24, his choice A;
  specs/quick-start.v1.md). One fixed script for the shell's spaces (work, personal, desktop, terminal,
  browser): `check` (each space's units, port, state and its one fix line), `install` (what is missing, from
  this checkout's own installers; the RDP kit's desktop is only reported), `start` (`systemctl --user enable
  --now` for what isn't running), `restart <space>` (one space, never all). Idempotent, one UTF-8 JSON object
  out, only the owner's own user (no sudo, no model call). `buddy setup --remote line` prints the key line it is
  meant for, `restrict,command="…/buddy setup --remote"`, to copy (never added here). Under that line it reads
  `$SSH_ORIGINAL_COMMAND` and refuses anything but one verb and at most one space before any command runs.
  `buddy doctor` (and `--json`, with each space's whole check) has a row per space: ⚠️ when one isn't running.

## 20261122.4

**Upgrading from 20261119.5 or older: move your config first.** The buddy now reads its config from one
folder only, `$XDG_CONFIG_HOME/brahman/` (on every system `~/.config/brahman/` when that variable isn't
set), and its secrets file is now `buddy.env` there (was `.env`). It no longer looks in
`~/.config/work_brahman/`, nor at a `buddy.toml` in the folder you start it from. Without the move it
starts with no config. Copy your files over before the first start (the old ones stay, so a downgrade
still finds them); if you ran the buddy from a folder with its own `buddy.toml`, copy from that folder
instead of `work_brahman`. Then `buddy doctor` names both files it found.

Linux and macOS:

```sh
old="${XDG_CONFIG_HOME:-$HOME/.config}/work_brahman"; new="${XDG_CONFIG_HOME:-$HOME/.config}/brahman"
mkdir -p "$new" && chmod 700 "$new"
cp -n "$old/buddy.toml" "$new/buddy.toml"
cp -n "$old/.env" "$new/buddy.env" && chmod 600 "$new/buddy.env"
[ -f "$old/peers.toml" ] && cp -n "$old/peers.toml" "$new/peers.toml"
buddy doctor
```

Windows (PowerShell):

```powershell
$old = "$env:USERPROFILE\.config\work_brahman"; $new = "$env:USERPROFILE\.config\brahman"
New-Item -ItemType Directory -Force $new | Out-Null
Copy-Item "$old\buddy.toml" "$new\buddy.toml"
Copy-Item "$old\.env" "$new\buddy.env"
if (Test-Path "$old\peers.toml") { Copy-Item "$old\peers.toml" "$new\peers.toml" }
buddy doctor
```

- **Every chat app is optional: web only is a normal setup** (the owner, 2026-11-22: "Telegram/Lark/Feishu
  should all be optional"). With no Telegram, Lark or Feishu set up, `buddy serve` used to stop at once
  ("no channel", exit 2), and so did `buddy short`, `ask`, `team` and the others. Now each chat turns
  itself on when its token exists, and with none the buddy runs as the workbench alone: the scheduler,
  short and long stories, and their cards and asks on the page (its board and "Waiting for you"; the
  web chat gets them as messages too when `[notify] web` is on), web push and the event stream as
  before. `buddy init` with no chat writes a web-only setup; `buddy config`, the TUI and the Setup tab
  save one with no chat left; `buddy doctor` says "✅ no chat app: web only", and ❌ only when there is
  no `[web] port` either. A session behind the gate (BUDDY_GATE) still holds no chat on purpose.

- **The owner secret: one secret for personal use** (the owner, 2026-11-21: "one secret", "it should all be
  localhost", "owner.secret is enough"). `buddy auth new` prints a secret once and keeps only its SHA-256
  hex in `~/.config/brahman/owner.secret` (0600). While that file exists the buddy checks it first (the
  workbench's cookie, login page and ?token=, Bearer, the event stream) and is loopback only ([web] hosts
  not honoured); BUDDY_WEB_TOKEN and device tokens still work after it. The hash is never a credential;
  the cookie is an HMAC of it under a random key in the state, so the file alone logs nobody in. `buddy
  doctor`: "auth: owner secret …: one secret for web, events, terminal, Browser, assistant", the old
  secrets still set, and `buddy auth new --force` for a leaked one. The assistant reads the same file
  itself and checks it first too. Without the file, everything is as before.

- **A private story never shows itself in a notification** (the PO, 2026-11-21, on the owner's "check the
  private stories in notifications"): the event stream (GET /api/events, what device tokens and the
  shell's native notifications read) and web push carried a 🔒 story's title and card line as they were.
  Now a private story or team (one not found too: fail closed) keeps its kind, ref, id and tag, but its
  title is "🔒 A private story changed" (or team) and its body and link are empty. A team is private
  until marked 🔓, so its card's notifications read that way too. privacy.private_ref is the one judge
  of a ref, for the page as well.

- **brahman's config in one folder** (the owner, 2026-11-21: "they're spread out too much"): the buddy
  reads `$XDG_CONFIG_HOME/brahman/buddy.toml` (default `~/.config/brahman/`, made 0700) and its secrets
  from `buddy.env` beside it (0600; was `.env`); `peers.toml` sits there too. One fixed place: a
  `buddy.toml` in the current folder (a checkout's) is no longer read, nor `~/.config/work_brahman/`;
  `-c PATH` names another (its `buddy.env` beside it), and the scripts take `BUDDY_TOML`. `buddy doctor`
  names both files and says `chmod 600` for a looser secrets file. `.env.example` is now
  `buddy.env.example`. The sandboxed agents' view hides the whole folder. The assistant reads
  `$XDG_CONFIG_HOME/brahman/assistant.toml` (was `personal_assistant/`), its own file only.

- **`buddy-team merge DUP NAME`** (the RDP RM, 2026-11-20: the conversion left shadow on the card twice,
  and once the duplicate `freerdp-shadow-comp-g4` was retired the real record couldn't take that name):
  a retired duplicate on no team folds into the live member, its whole record kept inside it and a copy
  in teams/merged/; the member may then `rename` to a name held only in that history, still nobody
  else's to take.

- **The team card holds many comps** (the PO, 2026-11-20, from the RDP RM's evidence: the card keyed a
  member by a role word global across teams, so one team couldn't hold two comps, a rename hit
  another team's `rel`, and members that left stayed forever). A member is keyed by its **lineage**,
  its name without `-g<N>`, unique across every team; its role (rel, po, comp, rev, dbg, edu) and
  area are fields: one rel and one po per team, any number of the others. The card sorts by role,
  then area. The commands go by name: `buddy-team new KIT --rel NAME`, `join KIT NAME --role comp
  --area A`, `handover OLD NEW`, `rename OLD NEW`, `set NAME --role/--area`, `retire KIT NAME` (off
  the card, the record kept; a retired lineage may return as its next generation); `leave` and
  `--kind` are gone. The records of before are converted once at the first load (roles/ →
  members/, the old files kept in roles.pre-lineage/), every card keeping its history.

- **Team names put the area after the role**: `<context>-<role>[-<area>]-g<N>` (`ghostty-comp-win-g1`,
  `freerdp-comp-audio-g1`), so a project's members also sort by role. A name given before keeps its
  form until its next generation, which takes the new one.

## 20261119.5

- **The short-story rule `buddy setup` installs says more of what a session needs**: `--who`, forks and
  loops, `attach` and the line the buddy types into the window, private chats only, no story from a
  subagent, one story across sessions and child stories. And the rules no longer claim a line in your
  `CLAUDE.md` wins: Claude Code combines the two, so to change a rule, edit it or leave it out.
- **The peers as instructions go into their own rules file**, `~/.claude/rules/brahman-peers.md`, no
  longer a block in `~/.claude/CLAUDE.md` (`buddy peer setup --instructions`; `--off` removes it). No
  part of the buddy edits a `CLAUDE.md` any more. Codex keeps its block in `AGENTS.md`.
- **Team names put the context first**: `<context>-<role>-g<N>` (`rdp-po-g2`, `shadow-comp-g4`), so window
  lists, pys groups and a restore sort by project. `buddy-team rename <role> <old> <new>` renames a live
  member on its card and keeps its generation; there is no alias for the old name.

## 20261119.4

- **The buddy's own settings (`.env`: the chat apps' secrets, the web token) no longer reach a pys window
  it opens.** Before, when no pys was running, the buddy's own pys call started it, and that pys and
  every window opened in it had the buddy's environment, `.env` included. `/topys` now never starts pys
  itself: with none running, it says to start it first.
- **A chat turn's agent no longer gets the buddy's web token, peer secret or other `.env` settings.**
  Before, only the chat apps' own secrets were kept from it. So `.env` is for the buddy's own keys: a
  setting meant for claude (an API key, a proxy) belongs in your shell or Claude Code's own config.
- **"✅ Always: <rule> in this project"** on a long story's permission card, beside Allow and Deny,
  when Claude Code suggests a rule narrower than a whole tool (`Bash(npm test *)`, never all of `Bash`):
  it allows this time and writes that rule into the project's `.claude/settings.local.json`, so the
  session stops asking for it there. Never into your user settings.
- **Windows: `/topys` works** ("pys: command not found: bash"): the buddy hands the session over itself
  and opens a pys window running the session (`claude --resume`): on Linux and macOS through your login
  shell, which stays when the session ends; on Windows claude itself, with no shell in between. While the buddy is still finishing a turn, `/topys` answers "handing over" at once and says
  the window when it opens. On Windows it needs a pys that can pass an environment (`pys new --env`,
  py-screen 20261119.2); an older one is named. `/frompys` that would end a terminal's session by force
  is refused on Windows (it would be a hard kill there): end it in its window, then `/frompys`.
- **UTF-8 everywhere, not the system's encoding**: every file the buddy reads or writes and every
  program's output it reads names UTF-8 (a stray bad byte shows as �, never an error), and the Python
  programs it starts run in UTF-8 mode. On a Chinese Windows (GBK) a program's Chinese or emoji output
  could raise an error or come back garbled; the tests now fail on a call that leaves it out.

## 20261119.3

- **`buddy doctor`'s "can't look for a second buddy" warning goes away by itself** once the app's chats
  can be listed again (Lark's monthly quota back, or the scope granted), at the next start.

## 20261119.2

- **Another buddy on the same bot is noticed**: when two buddies use one Feishu or Lark bot, Feishu gives
  each message to only one of them, so answers seem to come and go. The buddy now looks once at start
  (one call per linked chat: what its bot sent that it didn't) and on any press of a card it never sent,
  then tells you once in chat, and `buddy doctor` shows it until a clean start. No timer, so the
  monthly message quota is spared; if the app may not list messages, the doctor names the scope.
  While the app's monthly quota is used up, the start check is refused and the doctor shows it as ⚠️.
- **`buddy doctor` says when this buddy is older** than one it knows of without asking the network: a
  newer buddy that already ran with the same state folder, or a newer install (uv tool, pipx) on this
  machine. An older copy starting no longer posts its old notes as "what's new".

## 20261119.1

- **Windows: a long story's messages never touch the disk**: they go to its live session through a
  named pipe with a random key made for that story, in place of the file 20261118.2 used. Both ends
  prove they hold the key, so another program is refused, and a local program that connects and
  stalls can't hold the story up. A live session still running from 20261118.2 no longer takes
  messages: resume it. Linux and macOS are unchanged (a FIFO, as before).
- **Windows: `codex` starts without `cmd.exe`**: the buddy runs node with codex's own script, as its
  npm shim would, so a folder or an argument with `&` or a line break reaches it whole. It reads only the
  shim's own launch line, so an npm install that keeps node next to its shims works too. A shim it
  can't read still runs through `cmd.exe`, and `buddy.log` says so.
- **Windows, still not tested**: stopping a live session with ■ Stop, and handing a live session to a
  terminal.

## 20261118.2

- **Windows: `claude` and `codex` are never taken from the current folder**: Windows' program lookup
  looks there first, so a `claude.cmd` placed in a story's repository would have run as you. Only
  your PATH is searched now.
- **Which buddy answered**: an error reply ("⚠️ buddy error (laptop · 20261118.2): …") names the buddy and
  its version, so two buddies on one Feishu or Lark bot (each message goes to only one of them) no
  longer look like one flaky buddy. The name is `[buddy] name`, else the machine's short name. A "Not
  paired" reply, which may reach anyone, shows only a random id of this install and the version.
- **Windows: long stories work** (`/long new` failed with "module 'os' has no attribute 'mkfifo'"):
  where there are no FIFOs, the messages for a live session go through a file in your user profile,
  removed when the session ends. And the buddy now finds `claude` and `codex` as npm installs them
  on Windows (`claude.cmd` → its `claude.exe`): before, every chat turn there was "file not found".
- **Windows: a log file**: `buddy serve` also logs to `buddy.log` in its state folder (1 MB, three
  older ones kept), so when the buddy answers "⚠️ buddy error" in chat, the traceback is still there
  after its window scrolled.
- **Windows, not tested yet**: stopping a live session with ■ Stop, and handing a live session to a
  terminal. For now the messages for a live session wait in that file until the session ends; a
  later release moves Windows to a named pipe, so they never touch the disk.
- **Both loopbacks**: the workbench listens on `[::1]` as well as `127.0.0.1`, on the same port and
  nowhere else, so `localhost` reaches the buddy whichever address it resolves to, and no other
  program can take the other one. `GET /api/health` adds `instance`, a random id made at each start.
  If another program holds `[::1]` first, the buddy runs on, health says `"warn": "loopback6_taken"`,
  `buddy doctor` names the port, and you get one notice in your chats.
- **An event stream for other apps**: `GET /api/events` sends what web push sends, as Server-Sent
  Events (one JSON line per event, with an id the browser's push shares), resumes from
  `Last-Event-ID` and keeps the connection alive. A native app can read it with a device token that
  opens nothing else (`buddy token add <name>`, listed and revoked by name). Its own switch,
  `[notify] events` (off by default; also in Setup), works with web push off. The README says how.
- **Fixed: web push dropped the buddy's own posts** (a duty's post, the buddy's notices): sending
  one failed inside the buddy, so no browser got it. Story cards and questions were not affected.
- **Stale stories close themselves**: a new duty, `stale-stories` (`[duties.stale-stories]` with a
  schedule), closes a running story you asked to stop when nothing happened on it for a day and the
  session that ran it is gone (Claude Code no longer lists it and its window, if any, runs another process, or
  the Codex daemon doesn't know its thread). It ends as failed, noted "stopped by the owner; the
  session was gone". Anything unsure stays open: a story from another machine, one that names no
  session. Each run's record says how many it closed. By script, no model.
- **Fixed: a web token or a peer signature with non-ASCII characters** was an error inside the buddy
  (a 500); it is now refused like any wrong one.

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
