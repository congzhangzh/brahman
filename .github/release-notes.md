# brahman-buddy 20261126.1

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261126.1-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261126.1-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **A limited network is fine** (the owner, 2026-11-26: "hung and no serve", "buddy doctor itself hung!").
  `buddy serve` starts its web page first; the start-time notes (what's new, was down) go out beside it; every
  Lark/Feishu call has a timeout (the SDK had none), and a send that doesn't get through now simply fails (the
  card or post is not sent, nothing hangs or breaks; it is said once, as below). An app that can't be reached is said once, on every page
  and in `buddy doctor` ("Lark can't be reached since 10:02: the web page, the scheduler and the duties work"),
  and again when it is back ("Lark reached again at 10:14"). `buddy doctor` shows the local checks at once and
  gives the network checks 10 seconds together; one that doesn't answer is "not reached". Tested on Linux; not yet
  run on Windows.

- **Native push to your own apps through Apple (APNs)** (the owner, 2026-11-25: native push). Off by default:
  `[notify] apns = "ends"` or `"all"`, plus Apple's push key: the .p8 at `~/.config/brahman/apns.p8` (chmod 600)
  and `[apns] key_id`, `team_id`; without them it stays off and `buddy doctor` says what is missing. An app
  registers its device (POST `/api/push/apns`, behind the owner login); each event goes once to each device,
  with only its title (or `[notify] apns_text = "none"`: "brahman: news on #N"), the same choice and event id as
  web push; a token Apple no longer knows is dropped.

- **Each paired device has its own secret** beside the owner secret (the owner, 2026-11-25: one setup per
  device). A device makes its secret at its setup and leaves only its SHA-256 in
  `~/.config/brahman/owner.d/<device>.sha256`. The workbench and the assistant accept the owner secret or any
  device's, compared in constant time; deleting a device's file logs out that device and no other. A file
  that isn't one hex line, or a link, opens nothing. `buddy doctor` counts the devices and names a bad file.

- **The login forms name the owner secret**: while `owner.secret` is set up, the workbench's and the
  assistant's login forms ask for the owner secret, no longer a "web token" (without it, as before). A login
  from the shell's own port gets in too.

- **The quick start's fix step: `buddy setup --remote <verb> [<space>]`** (the owner, 2026-11-24, his choice A;
  specs/quick-start.v1.md). One fixed script for the shell's spaces (work, personal, desktop, terminal,
  browser): `check` (each space's units, port, state and its one fix line), `install` (what is missing, from
  this checkout's own installers; the RDP kit's desktop is only reported), `start` (`systemctl --user enable
  --now` for what isn't running), `restart <space>` (one space, never all). Idempotent, one UTF-8 JSON object
  out, only the owner's own user (no sudo, no model call). `buddy setup --remote line` prints the key line it is
  meant for, `restrict,command="…/buddy setup --remote"`, to copy (never added here). Under that line it reads
  `$SSH_ORIGINAL_COMMAND` and refuses anything but one verb and at most one space before any command runs.
  `buddy doctor` (and `--json`, with each space's whole check) has a row per space: ⚠️ when one isn't running.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
