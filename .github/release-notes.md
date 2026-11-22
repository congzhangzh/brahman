# brahman-buddy 20261122.4

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261122.4-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261122.4-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

**Upgrading from 20261119.5 or older: move your config first.** The buddy now reads its config from one
folder only, `$XDG_CONFIG_HOME/brahman/` (on every system `~/.config/brahman/` when that variable isn't
set), and its secrets file is now `buddy.env` there (was `.env`). It no longer looks in
`~/.config/work_brahman/`, nor at a `buddy.toml` in the folder you start it from. Without the move it
starts with no config. Copy your files over before the first start (the old ones stay, so a downgrade
still finds them); if you ran the buddy from a folder with its own `buddy.toml`, copy from that folder
instead of `work_brahman`. Then `buddy doctor` names both files it found.

Linux and macOS:

```sh
old="${XDG_CONFIG_HOME:-$HOME/.config}/work_brahman"; new="${XDG_CONFIG_HOME:-$HOME/.config}/brahman"
mkdir -p "$new" && chmod 700 "$new"
cp -n "$old/buddy.toml" "$new/buddy.toml"
cp -n "$old/.env" "$new/buddy.env" && chmod 600 "$new/buddy.env"
[ -f "$old/peers.toml" ] && cp -n "$old/peers.toml" "$new/peers.toml"
buddy doctor
```

Windows (PowerShell):

```powershell
$old = "$env:USERPROFILE\.config\work_brahman"; $new = "$env:USERPROFILE\.config\brahman"
New-Item -ItemType Directory -Force $new | Out-Null
Copy-Item "$old\buddy.toml" "$new\buddy.toml"
Copy-Item "$old\.env" "$new\buddy.env"
if (Test-Path "$old\peers.toml") { Copy-Item "$old\peers.toml" "$new\peers.toml" }
buddy doctor
```

- **Every chat app is optional: web only is a normal setup** (the owner, 2026-11-22: "Telegram/Lark/Feishu
  should all be optional"). With no Telegram, Lark or Feishu set up, `buddy serve` used to stop at once
  ("no channel", exit 2), and so did `buddy short`, `ask`, `team` and the others. Now each chat turns
  itself on when its token exists, and with none the buddy runs as the workbench alone: the scheduler,
  short and long stories, and their cards and asks on the page (its board and "Waiting for you"; the
  web chat gets them as messages too when `[notify] web` is on), web push and the event stream as
  before. `buddy init` with no chat writes a web-only setup; `buddy config`, the TUI and the Setup tab
  save one with no chat left; `buddy doctor` says "✅ no chat app: web only", and ❌ only when there is
  no `[web] port` either. A session behind the gate (BUDDY_GATE) still holds no chat on purpose.

- **The owner secret: one secret for personal use** (the owner, 2026-11-21: "one secret", "it should all be
  localhost", "owner.secret is enough"). `buddy auth new` prints a secret once and keeps only its SHA-256
  hex in `~/.config/brahman/owner.secret` (0600). While that file exists the buddy checks it first (the
  workbench's cookie, login page and ?token=, Bearer, the event stream) and is loopback only ([web] hosts
  not honoured); BUDDY_WEB_TOKEN and device tokens still work after it. The hash is never a credential;
  the cookie is an HMAC of it under a random key in the state, so the file alone logs nobody in. `buddy
  doctor`: "auth: owner secret …: one secret for web, events, terminal, Browser, assistant", the old
  secrets still set, and `buddy auth new --force` for a leaked one. The assistant reads the same file
  itself and checks it first too. Without the file, everything is as before.

- **A private story never shows itself in a notification** (the PO, 2026-11-21, on the owner's "check the
  private stories in notifications"): the event stream (GET /api/events, what device tokens and the
  shell's native notifications read) and web push carried a 🔒 story's title and card line as they were.
  Now a private story or team (one not found too: fail closed) keeps its kind, ref, id and tag, but its
  title is "🔒 A private story changed" (or team) and its body and link are empty. A team is private
  until marked 🔓, so its card's notifications read that way too. privacy.private_ref is the one judge
  of a ref, for the page as well.

- **brahman's config in one folder** (the owner, 2026-11-21: "they're spread out too much"): the buddy
  reads `$XDG_CONFIG_HOME/brahman/buddy.toml` (default `~/.config/brahman/`, made 0700) and its secrets
  from `buddy.env` beside it (0600; was `.env`); `peers.toml` sits there too. One fixed place: a
  `buddy.toml` in the current folder (a checkout's) is no longer read, nor `~/.config/work_brahman/`;
  `-c PATH` names another (its `buddy.env` beside it), and the scripts take `BUDDY_TOML`. `buddy doctor`
  names both files and says `chmod 600` for a looser secrets file. `.env.example` is now
  `buddy.env.example`. The sandboxed agents' view hides the whole folder. The assistant reads
  `$XDG_CONFIG_HOME/brahman/assistant.toml` (was `personal_assistant/`), its own file only.

- **`buddy-team merge DUP NAME`** (the RDP RM, 2026-11-20: the conversion left shadow on the card twice,
  and once the duplicate `freerdp-shadow-comp-g4` was retired the real record couldn't take that name):
  a retired duplicate on no team folds into the live member, its whole record kept inside it and a copy
  in teams/merged/; the member may then `rename` to a name held only in that history, still nobody
  else's to take.

- **The team card holds many comps** (the PO, 2026-11-20, from the RDP RM's evidence: the card keyed a
  member by a role word global across teams, so one team couldn't hold two comps, a rename hit
  another team's `rel`, and members that left stayed forever). A member is keyed by its **lineage**,
  its name without `-g<N>`, unique across every team; its role (rel, po, comp, rev, dbg, edu) and
  area are fields: one rel and one po per team, any number of the others. The card sorts by role,
  then area. The commands go by name: `buddy-team new KIT --rel NAME`, `join KIT NAME --role comp
  --area A`, `handover OLD NEW`, `rename OLD NEW`, `set NAME --role/--area`, `retire KIT NAME` (off
  the card, the record kept; a retired lineage may return as its next generation); `leave` and
  `--kind` are gone. The records of before are converted once at the first load (roles/ →
  members/, the old files kept in roles.pre-lineage/), every card keeping its history.

- **Team names put the area after the role**: `<context>-<role>[-<area>]-g<N>` (`ghostty-comp-win-g1`,
  `freerdp-comp-audio-g1`), so a project's members also sort by role. A name given before keeps its
  form until its next generation, which takes the new one.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
