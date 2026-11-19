# brahman-buddy 20261119.1

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261119.1-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261119.1-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

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

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
