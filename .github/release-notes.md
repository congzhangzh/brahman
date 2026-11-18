# brahman-buddy 20261117.3

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261117.3-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261117.3-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new


- **What a step changed and what it cost**: the detail panel shows a step's effects, by rules on
  the command run (git commit, push, force-push, merge, rebase, tag, `reset --hard`; gh and glab
  releases, PRs, merges and comments; systemctl; `rm -r`; docker and podman push, npm, uv and twine
  publish, `heads publish --yes`; story cards; an MCP tool by its verb). Names only, and an irreversible one is marked: ⚠ on its step in Plan, drawn apart in
  System. Your own rules go first, from `~/.config/brahman/effects.json`. The cost comes from the
  marking session's own transcript, between its "doing" and the mark that ends the step: calls,
  tokens in, out and cache, active time (a gap between two entries counts 5 minutes at most) and wall time.
  `$` shows only when `[views.prices]` in buddy.toml names the model's prices; the session's
  subagents are not counted, and the panel says so. Commands are read as the shell runs them: several
  lines, `sudo`, `timeout`, `git -C`, `gh -R` and quotes are understood. A rule of your own stores
  only its program unless it names its `"what"`, so a host or a path in your command never reaches
  the story.
- **Health check**: `GET /api/health` answers 200 while the buddy works (its heartbeat is fresh,
  every chat channel still listens, the scheduler runs) and 503 when a part fails, so `curl -f`
  alone tells. Without the web token it says only whether it works; with it, which part and the
  version too.
- **A link to one session**: `#session=3` opens the workbench on that long story (one Claude Code
  or Codex session), as `#story=12` and `#team=<kit>` open a story or a team; picking its tile
  writes it. With a web token set, the login page now keeps a link's `#…` part (it went to the plain
  page before).
- **Team roles**: `comp` is a **component developer**; "member" stays the word for any long-lived
  team session.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
