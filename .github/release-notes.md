# brahman-buddy 20261110.1

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261110.1-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261110.1-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **`buddy setup` only looks for pys now.** It never installs, moves or sets it up: the pys on a
  machine may be a work in progress run from a checkout, and setup cannot tell it from a release.
  Found: one line, and `pys setup` is yours to run. Missing: a highlighted warning whose hint says
  how to get it (`uv tool install py-screen`, then `pys setup --yes`; or pys's own bundle). The
  warning is not work to do: setup still exits 0. The offline workbench archive still carries pys.
- **A skill link into a checkout of the buddy that is still there is left alone** (another clone,
  a worktree: work in progress), whether setup runs from a checkout, an installed package or the
  one-file executable. Only a link that leads nowhere, or into an installed package, is renewed.
- **Every link setup does not make is a ⚠ warning with a hint**: why, and what to do to have the
  buddy's own (remove the link itself; rename your same-named skill to `<name>.mine`; on Windows,
  turn on Developer Mode where symlinks are not allowed), then `buddy setup --yes`. Yellow on a
  terminal, plain in a pipe or with `NO_COLOR`.
- The move from the old package name `py-screen-mux` is gone: install `py-screen` yourself.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
