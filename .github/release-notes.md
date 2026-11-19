# brahman-buddy 20261119.2

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261119.2-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261119.2-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **Another buddy on the same bot is noticed**: when two buddies use one Feishu or Lark bot, Feishu gives
  each message to only one of them, so answers seem to come and go. The buddy now looks once at start
  (one call per linked chat: what its bot sent that it didn't) and on any press of a card it never sent,
  then tells you once in chat, and `buddy doctor` shows it until a clean start. No timer, so the
  monthly message quota is spared; if the app may not list messages, the doctor names the scope.
  While the app's monthly quota is used up, the start check is refused and the doctor shows it as ⚠️.
- **`buddy doctor` says when this buddy is older** than one it knows of without asking the network: a
  newer buddy that already ran with the same state folder, or a newer install (uv tool, pipx) on this
  machine. An older copy starting no longer posts its old notes as "what's new".

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
