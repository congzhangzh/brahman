# brahman-buddy 20261117.2

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261117.2-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261117.2-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new


- **Three views of a story**: the workbench's Level 2 has Plan, System and Lanes on one shared
  selection: a step picked in any of them lights in all three, and one detail panel under them shows
  who did it and where, when, how long it waited for you, what it used and made (`--in`/`--out`),
  your tools and its note. Plan draws the steps in order (improvised ones dashed); System is the
  layout lit by the run (you on top, the input, the sessions, the effects, and under them skills, MCP
  servers, your CLIs, the web); Lanes gets a lane of your own on top: your answers, replies and
  ■ Stop as points, and how long each question waited for you.
- **Your tools on a step**: `buddy-short tools install` adds a Claude Code PostToolUse hook (a dated
  copy of settings.json first; `buddy-short tools uninstall` removes it). In a session that marks a
  running story it records, by rules, the skills, MCP servers, your own CLIs and the web the step
  used: names only, never the agent's built-in tools. Their arguments are copied apart from the
  story (readable by you only) and shown only behind ⚙ → 🐞 debug: arguments.
- **Links to the source**: a `--in`/`--out` value becomes a link only when its repo is certain: a
  URL; or `run`, `pr`, `mr`, `issue`, `sha`, `commit`, `release`, `tag` when the value names its repo
  (`owner/name#123`, `repo=owner/name`) or the checkout has exactly one GitHub or GitLab remote.
  Otherwise it stays plain text and the mark says how to name the repo. `--in` and `--out` are
  linked apart, and a value set again keeps no old link unless the new mark links it. Links show in
  the detail panel, on the cards and in the terminal, never in the public view.
- **Private or public, per story and team**: each short and long story is tagged when its text
  changes, by `brahman-privacy-mark` (your own on PATH or in `~/bin`, else the shipped sample: rules
  only; a missing, failing or slow marker means private). Teams are private until marked. Mark by
  hand with `buddy-short|buddy-long|buddy-team private <id> on|off`, or 🔒/🔓 on the page.
- **Public view** for a screen recording: the header's switch, for 3 hours in that browser, leaves out
  every private story and team, every duty run and question, the chat from before it and Setup, and
  sends no machine names, no links, and no URLs or repo names in a value's text; a red bar says so,
  and a line above the list says how many items it shows of how many there are. It never turns
  private by itself: when it ends, the page clears and asks.
- **⭐ Demo**: tag stories and teams for demos (`buddy-short|buddy-long|buddy-team demo <id> on`, or ⭐
  under ⚙), and the header's ☆ Demo shows only those; a private one stays hidden in the public view.
  A line above the list says when a filter hides items, with a ✕ to turn ☆ Demo off.
- **A quieter page**: the normal view keeps reading, your answers, ■ Stop and the reply boxes;
  ⚙ Advanced (kept per browser) adds the page's settings, Setup and each item's actions in one ⋯ menu.
- **After an outage**: a start more than 5 minutes after the last heartbeat posts one "I was down from
  … to …" notice (following `[notify]`), and each incoming message or press is logged, never its text.
- **Faster `buddy-short`**: a mark takes about 0.3 s instead of 3 to 8 s: Lark's SDK loads only when a
  Lark call is made, and a Telegram address family whose connect timed out is tried last for a week.
- **A Lark or Feishu reply** no longer fails with "'_thread._local' object has no attribute 'code'"
  when it is the first call of a new thread and the call worked.
- **The team rule** is installed by `buddy-team setup` as its own file, `~/.claude/rules/brahman-team.md`
  (Codex: a managed block in AGENTS.md, or nothing when a generator already copies the rules file).
  It now says that automatic work is script only, and has the edu role (one project, several or all).

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
