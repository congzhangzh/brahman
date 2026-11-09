# brahman-buddy 20261108.5

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261108.5-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **Native ARM64 executables for Linux and Windows**: `buddy-linux-arm64` and
  `buddy-windows-arm64.exe` beside the x86-64 ones (macOS has had Apple-silicon builds all
  along). Checked before the release on a real Windows 11 ARM64 (built natively there; the
  .exe's header says ARM64) and on Linux ARM64.
- **Every `buddy` command starts faster**: the Lark SDK is now loaded only by what talks to
  Lark. It used to cost 1.5 s on every command, `buddy-short` included (0.17 s now), and about
  28 s under Wine.
- **Windows**: looking for the agent session's `claude` process no longer calls `os.getppid`
  there (it found nothing on Windows anyway); under Wine that call crashed `buddy short start`.
- The release's CI fails a stuck build after 20 minutes instead of GitHub's 6 hours.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
