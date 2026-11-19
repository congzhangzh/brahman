# brahman-buddy 20261119.5

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261119.5-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261119.5-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **The short-story rule `buddy setup` installs says more of what a session needs**: `--who`, forks and
  loops, `attach` and the line the buddy types into the window, private chats only, no story from a
  subagent, one story across sessions and child stories. And the rules no longer claim a line in your
  `CLAUDE.md` wins: Claude Code combines the two, so to change a rule, edit it or leave it out.
- **The peers as instructions go into their own rules file**, `~/.claude/rules/brahman-peers.md`, no
  longer a block in `~/.claude/CLAUDE.md` (`buddy peer setup --instructions`; `--off` removes it). No
  part of the buddy edits a `CLAUDE.md` any more. Codex keeps its block in `AGENTS.md`.
- **Team names put the context first**: `<context>-<role>-g<N>` (`rdp-po-g2`, `shadow-comp-g4`), so window
  lists, pys groups and a restore sort by project. `buddy-team rename <role> <old> <new>` renames a live
  member on its card and keeps its generation; there is no alias for the old name.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
