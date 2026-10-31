# brahman-buddy 0.2.6

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-0.2.6-py3-none-any.whl`) |
| nothing, Linux x86-64 | `buddy-linux-x86_64` | `chmod +x` it, then run it |
| nothing, Windows x64 | `buddy-windows-x64.exe` | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- The package is now **brahman-buddy**, on PyPI: `uv tool install brahman-buddy`. The command
  is still `buddy`. (0.2.5 and earlier were named work-brahman and are not on PyPI.)

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
