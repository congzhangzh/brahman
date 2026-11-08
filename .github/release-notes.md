# brahman-buddy 20261108.3

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261108.3-py3-none-any.whl`) |
| nothing, Linux x86-64 | `buddy-linux-x86_64` | `chmod +x` it, then run it |
| nothing, Windows x64 | `buddy-windows-x64.exe` | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

The first date-numbered release: versions are now `YYYYMMDD.N` (the day it was picked, then
its number that day). It sorts above 0.2.6, so `uv tool upgrade brahman-buddy` moves you on.

- **Short stories wake their session.** `buddy-short start` (or `attach N`) records the
  Claude Code window it runs in; when the owner replies, answers or presses Stop, `buddy serve`
  types one fixed line there, only while that window's input box is idle and empty. Codex
  sessions get the same line through `codex queue`, only while a Codex terminal holds the
  thread. The doctor says whether Codex's own server is up.
- **Window titles name the work.** Short stories put `#N` into the pys window title, kept
  short (about 24 characters), through `pys title --owner`, so a title goes when its session ends.
- **Short stories read three ways:** stable step ids, who marked a step, what a step used and
  made (`--in` / `--out`); a step may link one child story (`--parent`).
- **Teams:** `buddy team` keeps one live card per kit, a row per long-lived role (session,
  window, age, book, running stories, last handovers).
- **Long stories:** the buddy holds a live agent session you drive from the phone
  (`/newlong`, `/status`, `/handover`, `/takeback`); any folder from the card; sessions it
  starts run behind a gate, sandboxed (bubblewrap, no credentials in sight) or open, chosen
  per session.
- **Housekeeping:** the buddy's child processes live in one `buddy.slice`; `brahman-backup`
  takes a daily dated snapshot of the buddy's state; the Lark SDK's log goes to stderr, not
  stdout; the README shows the workbench from another computer through `ssh -L`.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
