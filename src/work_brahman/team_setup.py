"""`buddy team setup`: write the team rule (member names, window titles, generations) into the
global instructions as a managed block (the owner, 2026-11-11: "make it global instruction and install
by brahman"), the way `samvada setup` manages its own.

The block sits between `brahman-team:begin` and `brahman-team:end` in Claude Code's CLAUDE.md
and Codex's AGENTS.md. Its text is families/team/RULE.md; it points at families/team/TEAM.md
(the detail) by this checkout's path. Rerunnable: a block already there is replaced in place,
so it keeps the place the owner gave it; a file without one gets it at the end. Nothing outside
the markers is ever changed. A copy of the file is kept beside it before a change
(<name>.brahman-backup-<time>). `--dry-run` prints the plan and the block and changes nothing;
`--uninstall` removes exactly the block.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent / "families" / "team"
BEGIN = "<!-- brahman-team:begin (managed by `buddy-team setup`; change brahman's families/team/RULE.md, not this block) -->"
END = "<!-- brahman-team:end -->"
BLOCK_RE = re.compile(r"<!-- brahman-team:begin.*?<!-- brahman-team:end -->\n?", re.S)
AGENTS = ("claude", "codex")


def _home_path(p: Path, env: Mapping[str, str]) -> str:
    """~/… for a path under the home folder, so the block reads the same on every machine."""
    home = Path(env.get("HOME") or Path.home())
    try:
        return "~/" + str(p.relative_to(home))
    except ValueError:
        return str(p)


def render(env: Mapping[str, str] = os.environ) -> str:
    body = (HERE / "RULE.md").read_text().replace("{detail}", _home_path(HERE / "TEAM.md", env))
    return f"{BEGIN}\n{body.rstrip()}\n{END}\n"


def rule_file(agent: str, env: Mapping[str, str] = os.environ) -> Path:
    if agent == "claude":
        base = Path(env["CLAUDE_CONFIG_DIR"]) if env.get("CLAUDE_CONFIG_DIR") else Path.home() / ".claude"
        return base / "CLAUDE.md"
    base = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else Path.home() / ".codex"
    return base / "AGENTS.md"


def new_text(old: str, block: str, uninstall: bool = False) -> str:
    """The file with the block put in (in place, else at the end) or taken out."""
    if uninstall:  # the blank lines before it go too: an install then an uninstall gives the file back
        s = re.sub(r"\n*" + BLOCK_RE.pattern, "\n", old, count=1, flags=re.S)
        return s.lstrip("\n") if old.startswith("<!-- brahman-team:begin") else s
    if BLOCK_RE.search(old):
        return BLOCK_RE.sub(lambda _: block, old, count=1)
    return (old.rstrip("\n") + "\n\n" if old.strip() else "") + block


def plan(agents: tuple[str, ...], uninstall: bool = False,
         env: Mapping[str, str] = os.environ) -> list[tuple[Path, str, str | None]]:
    """(file, what would happen, its new text or None when nothing changes), per agent."""
    block = render(env)
    out = []
    for agent in agents:
        f = rule_file(agent, env)
        old = f.read_text() if f.exists() else ""
        new = new_text(old, block, uninstall)
        if new == old:
            out.append((f, "no team block in" if uninstall else "unchanged in", None))
        elif uninstall:
            out.append((f, "remove the team block from", new))
        else:
            out.append((f, "update the team block in" if BLOCK_RE.search(old) else "add the team block to", new))
    return out


def run(agents: tuple[str, ...], dry_run: bool = False, uninstall: bool = False,
        env: Mapping[str, str] = os.environ, out=print) -> int:
    steps = plan(agents, uninstall, env)
    for f, what, _ in steps:
        out(f"{what} {f}")
    if dry_run:
        if not uninstall:
            out("\nthe block:\n\n" + render(env))
        out("dry run: nothing changed")
        return 0
    for f, _, new in steps:
        if new is None:
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists():
            shutil.copy2(f, f.with_name(f"{f.name}.brahman-backup-{time.strftime('%Y%m%d-%H%M%S')}"))
        f.write_text(new)
    if all(new is None for _, _, new in steps):
        out("nothing to change")
    return 0
