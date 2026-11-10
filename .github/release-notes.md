# brahman-buddy 20261110.5

A personal work buddy: Telegram, Lark and Feishu chats driving Claude Code, with scheduled
jobs, short stories (one card per piece of pipeline-bound work) and long stories (an agent
session followed from the phone).

## Install

| You have | Take | Then |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | PyPI | `uv tool install brahman-buddy`, later `uv tool upgrade brahman-buddy` (or the wheel here: `uv tool install ./brahman_buddy-20261110.5-py3-none-any.whl`); in the first minutes after a release add `--refresh`, since uv keeps PyPI's list of versions for a while |
| nothing, Linux | `buddy-linux-x86_64` (x86-64) or `buddy-linux-arm64` (ARM64) | `chmod +x` it, then run it |
| nothing, Windows | `buddy-windows-x64.exe` (x64) or `buddy-windows-arm64.exe` (ARM64) | run it from a terminal |
| nothing, macOS | `buddy-macos-arm64` (Apple silicon) or `buddy-macos-x86_64` (Intel) | `chmod +x` it; the first run may need System Settings → Privacy & Security → Open Anyway (it is not notarized) |
| a closed or strict network | `brahman-workbench-20261110.5-<platform>` (.tar.gz, or .zip on Windows): the buddy and pys, everything inside | unpack, then `./install.sh` (or `install.ps1`); nothing is downloaded |

Every way needs Claude Code too, installed and logged in. Then: `buddy init`, `buddy service
install --enable`, write to your bot, `buddy allow <the id it tells you>`, `buddy doctor`.

## What is new

- **Teams and short tasks link to each other on the workbench.** A team's view lists every
  member with its short tasks, running ones first, each one a button that opens that task. A
  short task names its member as a link that opens the team's view with that member's row
  highlighted. A member is found through every generation's session, so its work stays with the
  role after a handover. The view says when only the sessions in the team's records are listed.
  Links are in the address (`#story=<n>`, `#team=<team>&role=<role>`): they survive a reload and
  can be shared. A link to a task or team that isn't there says so, instead of quietly showing
  another one. Plain JavaScript, no framework.
- **Nothing drops off the page any more.** The short- and long-story rows hold every story in a
  scrolling box (before: the last 3 days and 40 tiles). The newest 40 short stories and the
  running ones come whole; older ones are light tiles, fetched whole when you pick one. Each team
  has a tile too: its joins and handovers, and its card when picked. Jobs and duties keep 3 days.
- **A post refused only by Lark's monthly quota is "quota" (⚠ 配额), not "failed" (✗)**, when it
  reached you on another chat. Older records count as quota only when the evidence says so; their
  files are not changed. The meter shows the last 7 days and all time, each with its start day.
- **Telegram keeps one 🏠 home topic.** It is forgotten only when Telegram says the topic is gone,
  not after any failed post (a dropped connection used to open a second one).
- **Long stories, from the workbench into a terminal (needs pys):** ▶ attach shows the pys
  window that holds the session; ↻ resume opens a window in its folder and runs
  `buddy-long take N` there. A Codex story shows the `codex resume` line to type (take is
  Claude Code's only). A long story's label is `[Claude] #7 title` or `[Codex] #6 title` on the
  page, and the name of each new Telegram topic (existing topics keep their names).
- The Chinese page names short stories 短链路任务 and long stories 完整 Claude/Codex; the English
  page is unchanged.

**Known issue:** without pys, ↻ resume gets no answer (the buddy keeps running). Install pys
(`uv tool install py-screen`) to use attach and resume; the next release answers the click.

## How it was checked

Each executable was built and then used on its own operating system by this release's
workflow (help, init, allow, doctor, a short story, the service, the status), before the
release was published. `SHA256SUMS` lists every file.
