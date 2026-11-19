"""`buddy team setup`: install the team rule (member names, window titles, generations, script-only
automatic work) into the global instructions, as brahman's own file (the owner, 2026-11-11: "make
it global instruction and install by brahman"; 2026-11-14: "not rewrite the global CLAUDE.md, but
extend it with an xxx.d or rules").

- **Claude Code** loads every `rules/*.md` of its config folder (`~/.claude`, or
  `$CLAUDE_CONFIG_DIR`) in every project, so the rule is one file there, `rules/brahman-team.md`,
  which setup alone writes and `--uninstall` deletes. The owner's CLAUDE.md is never touched.
- **Codex** has no such folder: it reads `AGENTS.md` (or `AGENTS.override.md`) in `~/.codex`
  (`$CODEX_HOME`) and nothing beside it. So:
  - an AGENTS.md built by a generator that copies `~/.claude/rules/*.md` into it already carries
    the file's first line (HEAD): setup leaves it alone and says whether it is current (rebuild it
    when not). A block there would be lost at the next rebuild, or would make a second copy;
  - any other AGENTS.md (hand-kept, or none) gets the rule as a managed block between
    `brahman-team:begin` and `brahman-team:end`, replaced in place when rerun, a copy kept beside
    it before a change (<name>.brahman-backup-<time>). Nothing outside the markers changes.

The text is families/team/RULE.md; it points at families/team/TEAM.md (the detail) by this
checkout's path. `--dry-run` prints the plan and the rule and changes nothing.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent / "families" / "team"
HEAD = ("<!-- brahman-team: written by `buddy-team setup` from brahman's families/team/RULE.md; "
        "change it there, not here -->")
BEGIN = "<!-- brahman-team:begin (managed by `buddy-team setup`; change brahman's families/team/RULE.md, not this block) -->"
END = "<!-- brahman-team:end -->"
BLOCK_RE = re.compile(r"<!-- brahman-team:begin.*?<!-- brahman-team:end -->\n?", re.S)
AGENTS = ("claude", "codex")
NAME = "brahman-team.md"


def _home_path(p: Path, env: Mapping[str, str]) -> str:
    """~/… for a path under the home folder, so the rule reads the same on every machine."""
    home = Path(env.get("HOME") or Path.home())
    try:
        return "~/" + str(p.relative_to(home))
    except ValueError:
        return str(p)


def body(env: Mapping[str, str] = os.environ) -> str:
    return (HERE / "RULE.md").read_text(encoding="utf-8").replace("{detail}", _home_path(HERE / "TEAM.md", env)).rstrip()


def render(env: Mapping[str, str] = os.environ) -> str:
    """Claude's rules file."""
    return f"{HEAD}\n\n{body(env)}\n"


def block(env: Mapping[str, str] = os.environ) -> str:
    """Codex's managed block, for an AGENTS.md no generator builds."""
    return f"{BEGIN}\n{body(env)}\n{END}\n"


def rule_file(agent: str, env: Mapping[str, str] = os.environ) -> Path:
    home = Path(env.get("HOME") or Path.home())
    if agent == "claude":
        base = Path(env["CLAUDE_CONFIG_DIR"]) if env.get("CLAUDE_CONFIG_DIR") else home / ".claude"
        return base / "rules" / NAME
    base = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else home / ".codex"
    return base / "AGENTS.md"


def new_text(old: str, blk: str, uninstall: bool = False) -> str:
    """The file with the block put in (in place, else at the end) or taken out."""
    if uninstall:  # the blank lines before it go too: an install then an uninstall gives the file back
        s = re.sub(r"\n*" + BLOCK_RE.pattern, "\n", old, count=1, flags=re.S)
        return s.lstrip("\n") if old.startswith("<!-- brahman-team:begin") else s
    if BLOCK_RE.search(old):
        return BLOCK_RE.sub(lambda _: blk, old, count=1)
    return (old.rstrip("\n") + "\n\n" if old.strip() else "") + blk


def generated(text: str) -> bool:
    """A generator copied the rules file into it (HEAD outside any managed block)."""
    return HEAD in BLOCK_RE.sub("", text)


Step = tuple[Path, str, "str | None", bool]  # file, what happens, new text (None: no write), delete


def _claude(f: Path, uninstall: bool, env: Mapping[str, str]) -> Step:
    if uninstall:
        return (f, "remove", None, True) if f.exists() else (f, "not there:", None, False)
    new = render(env)
    old = f.read_text(encoding="utf-8") if f.exists() else None
    if old == new:
        return f, "unchanged:", None, False
    return f, "write" if old is None else "update", new, False


def _codex(f: Path, uninstall: bool, env: Mapping[str, str]) -> Step:
    old = f.read_text(encoding="utf-8") if f.exists() else ""
    if generated(old):
        state = "current" if body(env) in old else "out of date, rebuild it with its generator"
        if uninstall:
            state = "rebuild it with its generator once the rules file is gone"
        return f, f"left alone (it copies ~/.claude/rules/{NAME}; {state}):", None, False
    new = new_text(old, block(env), uninstall)
    if new == old:
        return f, "no team block in" if uninstall else "unchanged in", None, False
    if uninstall:
        return f, "remove the team block from", new, False
    return f, "update the team block in" if BLOCK_RE.search(old) else "add the team block to", new, False


def plan(agents: tuple[str, ...], uninstall: bool = False,
         env: Mapping[str, str] = os.environ) -> list[Step]:
    """Claude first: a generator that builds Codex's file from Claude's reads the rules file."""
    return [(_claude if a == "claude" else _codex)(rule_file(a, env), uninstall, env) for a in agents]


def run(agents: tuple[str, ...], dry_run: bool = False, uninstall: bool = False,
        env: Mapping[str, str] = os.environ, out=print) -> int:
    steps = plan(agents, uninstall, env)
    for f, what, _, _ in steps:
        out(f"{what} {f}")
    if dry_run:
        if not uninstall:
            out("\nthe rule:\n\n" + render(env))
        out("dry run: nothing changed")
        return 0
    for f, _, new, delete in steps:
        if delete:
            f.unlink()
            continue
        if new is None:
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.name == "AGENTS.md" and f.exists():  # the owner's file: a copy before a change
            shutil.copy2(f, f.with_name(f"{f.name}.brahman-backup-{time.strftime('%Y%m%d-%H%M%S')}"))
        f.write_text(new, encoding="utf-8")
    if all(new is None and not delete for _, _, new, delete in steps):
        out("nothing to change")
    return 0
