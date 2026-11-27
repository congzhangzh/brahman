# brahman-buddy 20261126.3

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261126.3-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261126.3-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **No key slot = the workbench is closed** (fail closed). Until a slot exists (`buddy auth new --append <name>`
  on the machine), the page says just that and every API and action is refused, `BUDDY_WEB_TOKEN` included;
  `buddy serve` still runs the scheduler, the duties and the chats, and `buddy doctor` says what to run. Before,
  an install with no slot was open on the loopback.
- **Key slots for the owner login, like ZFS / LUKS key slots** (the owner, 2026-11-26). One secret per device or
  browser, each a named slot in `~/.config/brahman/owner.d/` (only its SHA-256 kept): `buddy auth new --append
  <name> [--note "…"]`, `--replace <name>` (its old secret stops at once), `--replace-all <name>` (every slot
  retired, one new), `buddy auth list`, `buddy auth remove <name>`. Only your own shell on the server runs
  them. The trust root is the first SSH key (the one that set the system up): the weak point by design, which
  keeps everything else simple.
- **Upgrading: do this before the restart.** `owner.secret` is no longer read, and nothing moves it for you. It
  holds only the SHA-256 of your secret, so copying the file keeps the same secret:
  `mkdir -m 700 -p ~/.config/brahman/owner.d && install -m 600 ~/.config/brahman/owner.secret
  ~/.config/brahman/owner.d/owner.sha256` (Windows, PowerShell: `New-Item -ItemType Directory -Force
  $HOME\.config\brahman\owner.d; Copy-Item $HOME\.config\brahman\owner.secret
  $HOME\.config\brahman\owner.d\owner.sha256`); or make a new one: `buddy auth new --append <name>`. Until a
  slot exists the workbench stays closed (`BUDDY_WEB_TOKEN` alone no longer opens it); `buddy doctor` says what
  to run.
- **`buddy-team` and `buddy-short` come with the package**, on every system, Windows too (they were scripts in the
  repository only).
- **`buddy setup` installs the team rule too** (`~/.claude/rules/brahman-team.md`, beside the git roles and the
  short stories; `--without brahman-team` leaves it out), and the README says what the three rules make
  Claude do and how a team works together, generations included.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
