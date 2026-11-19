# brahman-buddy 20261118.2

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261118.2-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261118.2-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

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

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
