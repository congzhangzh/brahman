# brahman-buddy 20261108.7

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261108.7-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **Python 3.14 or later.** The buddy now asks for Python 3.14+ (it supported 3.12+), the same
  floor as pys, so one Python serves both. `uv tool install brahman-buddy` fetches a 3.14 by
  itself when the machine has an older one; the one-file executables bring their own and need
  no Python at all. Every dependency has 3.14 wheels for Linux, Windows and macOS, x86-64 and
  ARM64, so nothing is compiled on install.
- **A demo, for every release from now on**: `buddy-demo.gif` and `buddy-demo.mp4` among the
  release's files (the README shows the latest), recorded in a clean container as a made-up
  user, with no network while it records.
- The release commits are now written by "brahman release", and the release's checks also
  read the commit's own metadata, not only its files.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
