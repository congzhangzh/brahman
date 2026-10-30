# work-brahman 0.2.3

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | `work_brahman-0.2.3-py3-none-any.whl` | `uv tool install ./work_brahman-0.2.3-py3-none-any.whl` |
| nothing, Linux x86-64 | `buddy-linux-x86_64` | `chmod +x` it, then run it |
| nothing, Windows x64 | `buddy-windows-x64.exe` | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- Releases are built and checked on every platform by GitHub Actions: Linux, Windows, macOS
  (Apple silicon and Intel), plus the wheel, all in one release. Each file goes into a draft
  release, which is published only when every platform passed.
- The buddy itself is the same as 0.2.1.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
