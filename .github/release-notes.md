# brahman-buddy 20261119.4

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261119.4-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261119.4-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **The buddy's own settings (`.env`: the chat apps' secrets, the web token) no longer reach a pys window
  it opens.** Before, when no pys was running, the buddy's own pys call started it, and that pys and
  every window opened in it had the buddy's environment, `.env` included. `/topys` now never starts pys
  itself: with none running, it says to start it first.
- **A chat turn's agent no longer gets the buddy's web token, peer secret or other `.env` settings.**
  Before, only the chat apps' own secrets were kept from it. So `.env` is for the buddy's own keys: a
  setting meant for claude (an API key, a proxy) belongs in your shell or Claude Code's own config.
- **"✅ Always: <rule> in this project"** on a long story's permission card, beside Allow and Deny,
  when Claude Code suggests a rule narrower than a whole tool (`Bash(npm test *)`, never all of `Bash`):
  it allows this time and writes that rule into the project's `.claude/settings.local.json`, so the
  session stops asking for it there. Never into your user settings.
- **Windows: `/topys` works** ("pys: command not found: bash"): the buddy hands the session over itself
  and opens a pys window running the session (`claude --resume`): on Linux and macOS through your login
  shell, which stays when the session ends; on Windows claude itself, with no shell in between. While the buddy is still finishing a turn, `/topys` answers "handing over" at once and says
  the window when it opens. On Windows it needs a pys that can pass an environment (`pys new --env`,
  py-screen 20261119.2); an older one is named. `/frompys` that would end a terminal's session by force
  is refused on Windows (it would be a hard kill there): end it in its window, then `/frompys`.
- **UTF-8 everywhere, not the system's encoding**: every file the buddy reads or writes and every
  program's output it reads names UTF-8 (a stray bad byte shows as �, never an error), and the Python
  programs it starts run in UTF-8 mode. On a Chinese Windows (GBK) a program's Chinese or emoji output
  could raise an error or come back garbled; the tests now fail on a call that leaves it out.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
