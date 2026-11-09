# brahman-buddy 20261108.10

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261108.10-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261108.10-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **`buddy setup`: the platform around the buddy**, in one command. A dry run unless `--yes`;
  run it again any time: what is ours is kept current, what you changed is left alone.
  - the buddy's agent skills into `~/.claude/skills` (linked; the one-file executable copies);
  - the working rules for agent sessions as drop-ins, `~/.claude/rules/brahman-git-roles.md`
    (branches by role, a status card before git work) and `brahman-short-stories.md` (report
    pipeline-bound work as a card); `--without <rule>` leaves one out and remembers it;
  - pys, the terminal multiplexer: installed as a uv tool, then its own `pys setup`.
- The workbench archives (for closed networks) run `buddy setup --yes --no-pys` too, so the
  rules come along offline.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
