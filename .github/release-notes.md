# brahman-buddy 20261113.1

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261113.1-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261113.1-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

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

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
