"""`buddy setup`: the personal engineering platform on this machine (specs/brahman-setup.md).

A dry run by default: it says what it would do; `--yes` does it. Run it again any time: it
brings what is ours up to date and leaves alone what is the user's.

- skills: the buddy's agent skills in ~/.claude/skills. An installed package links them (they
  follow every upgrade); the one-file executable unpacks into a folder that goes away when it
  exits, so it copies them, with a stamp recording what it wrote. A same-named skill that is
  neither our link nor our untouched copy is the user's: left alone. A link of ours that leads
  into a checkout that is still there (a work in progress: another clone, a worktree) is never
  moved to this buddy's copy. Every link setup does not make is a warning with a hint: why, and
  what to do to have ours.
- rules: the working rules as drop-ins in ~/.claude/rules/brahman-*.md, which Claude Code loads
  for every session. A file the user changed is theirs from then on; `--without <name>` leaves
  one out (and keeps it out). The user's own CLAUDE.md is read after them, so a line there wins.
- pys: the terminal multiplexer, its own package (py-screen) with its own install and its own
  `pys setup`. buddy setup never installs, moves or sets it up (2026-11-10: it may be a work in
  progress the user runs from a checkout): it says whether pys is there, and when it
  is not, a warning says how to get it (uv tool install py-screen, or pys's own bundle). The
  offline workbench archive still carries pys; its installer puts it in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

from .runs import VERSION

HERE = Path(__file__).resolve().parent
PYS_PACKAGE = "py-screen"  # pys's package, installed on its own (never by buddy setup)
PYS_BUNDLES = "https://github.com/congzhangzh/py-screen/releases/latest"
STAMP = ".brahman-buddy.json"  # in a skill we copied, and once in ~/.claude/rules: what we wrote


class Step(NamedTuple):
    """One line of the plan. do is None when there is nothing to do; a hint makes it a warning:
    something setup did not do, why, and what the user can do about it."""
    what: str
    state: str
    do: Callable[[], object] | None = None
    hint: str = ""


def _bundled(kind: str) -> Path | None:
    """The skills or rules this buddy carries: inside the package, or beside it in a checkout."""
    return next((p for p in (HERE / kind, HERE.parent.parent / kind) if p.is_dir()), None)


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _digest(path: Path) -> str:
    """One hash for a file, or for a folder's files (names and contents), our stamp left out."""
    h = hashlib.sha256()
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file() and p.name != STAMP)
    for f in files:
        h.update(str(f.relative_to(path) if path.is_dir() else f.name).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def _same(link: Path, target: Path) -> bool:
    """The link leads to that folder. Compared as real paths, not as the link's text: Windows
    spells a link's target its own way (\\\\?\\C:\\..., another case), so the text never matches
    and every run would relink (20261108.10, found on a real Windows 11)."""
    return os.path.normcase(os.path.realpath(link)) == os.path.normcase(os.path.realpath(target))


def _ours_link(dest: Path, kind: str) -> bool:
    """A link we made: it points into a buddy's own skills or rules (this install or an older one,
    even one that is gone now)."""
    if not dest.is_symlink():
        return False
    target = os.readlink(dest)
    return f"work_brahman{os.sep}{kind}{os.sep}" in target or f"brahman{os.sep}{kind}{os.sep}" in target


def _installed_or_gone(link: Path) -> bool:
    """A link of ours that setup may move: it leads nowhere now, or into an installed package
    (site-packages). One that leads into a checkout that is still there is someone's work in
    progress (another clone, a worktree), and stays."""
    if not os.path.exists(link):
        return True
    parts = {p.lower() for p in Path(os.path.realpath(link)).parts}
    return bool(parts & {"site-packages", "dist-packages"})


def _remove_hint(dest: Path) -> str:
    """How to remove a link itself, never what it leads to."""
    return f'rmdir "{dest}"' if os.name == "nt" else f'rm "{dest}"'


def _checkout_link(skill: Path, dest: Path) -> Step:
    return Step(f"skill {skill.name}", "linked to a checkout of ours: left alone (a work in progress?)", None,
                f"it leads to {os.path.realpath(dest)}, not to this buddy's {skill}. Keep it while you work "
                f"there; to use this buddy's instead: {_remove_hint(dest)} (the link only), then buddy setup --yes")


def _users_skill(skill: Path, dest: Path) -> Step:
    what = f"a link to {os.readlink(dest)}" if dest.is_symlink() else "a folder that is not our untouched copy"
    return Step(f"skill {skill.name}", "yours (changed or not ours): left alone", None,
                f"{dest} is {what}, so ours ({skill}) is not linked. To take ours: move yours aside "
                f"(rename it to {dest.name}.mine), then buddy setup --yes")


def skill_steps(home: Path) -> list[Step]:
    """A Step for each bundled skill."""
    src = _bundled("skills")
    if src is None:
        return [Step("skills", "none bundled with this buddy")]
    dest_dir = home / ".claude" / "skills"
    steps = []
    for skill in sorted(p for p in src.iterdir() if (p / "SKILL.md").exists()):
        dest = dest_dir / skill.name
        what = f"skill {skill.name}"
        want = _digest(skill)
        stamp = dest / STAMP
        if _frozen():  # the one-file's folder goes away when it exits: copy, with a stamp
            if not dest.exists() and not dest.is_symlink():
                steps.append(Step(what, "copy (new)", lambda s=skill, d=dest, w=want: _copy(s, d, w)))
            elif dest.is_symlink() and _ours_link(dest, "skills"):
                if _installed_or_gone(dest):
                    steps.append(Step(what, "replace our old link with a copy",
                                      lambda s=skill, d=dest, w=want: _copy(s, d, w)))
                else:
                    steps.append(_checkout_link(skill, dest))
            elif stamp.exists() and json.loads(stamp.read_text()).get("sha256") == _digest(dest):
                if _digest(dest) == want:
                    steps.append(Step(what, "up to date"))
                else:
                    steps.append(Step(what, "update our copy", lambda s=skill, d=dest, w=want: _copy(s, d, w)))
            else:
                steps.append(_users_skill(skill, dest))
            continue
        if dest.is_symlink() and _same(dest, skill):
            steps.append(Step(what, "up to date (linked)"))
        elif not dest.exists() and not dest.is_symlink():
            steps.append(Step(what, "link (new)", lambda s=skill, d=dest: _link(s, d)))
        elif _ours_link(dest, "skills"):
            if _installed_or_gone(dest):
                steps.append(Step(what, "relink (an older or gone install of ours)",
                                  lambda s=skill, d=dest: _link(s, d)))
            else:
                steps.append(_checkout_link(skill, dest))
        elif stamp.exists() and json.loads(stamp.read_text()).get("sha256") == _digest(dest):
            if _digest(dest) == want:  # our copy, current: where symlinks are not allowed (Windows
                steps.append(Step(what, "up to date (copied, not linked)", None,  # without Developer Mode)
                                  _NO_SYMLINK_HINT))
            else:  # our copy, older: a link if this system allows one, else a fresh copy
                steps.append(Step(what, "update our copy", lambda s=skill, d=dest: _link(s, d)))
        else:
            steps.append(_users_skill(skill, dest))
    return steps


_NO_SYMLINK_HINT = ("this system did not allow a symlink, so it is a copy: it changes only when buddy setup "
                    "--yes runs again. On Windows, turn on Developer Mode (Settings, System, For developers), "
                    "then buddy setup --yes links it")


def _copy(src: Path, dest: Path, digest: str) -> None:
    if dest.is_symlink():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)  # only ever our own untouched copy: checked against its stamp first
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    (dest / STAMP).write_text(json.dumps({"by": f"brahman-buddy {VERSION}", "sha256": digest}) + "\n")


def _link(src: Path, dest: Path) -> None:
    if dest.is_symlink():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)  # only ever our own untouched copy: checked against its stamp first
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.symlink_to(src, target_is_directory=True)
    except OSError as e:  # Windows without Developer Mode can't make symlinks: a copy then
        shutil.copytree(src, dest)
        (dest / STAMP).write_text(json.dumps({"by": f"brahman-buddy {VERSION}", "sha256": _digest(src)}) + "\n")
        print(f"  {_warn('⚠')} {dest.name}: copied, not linked ({e})\n      hint: {_NO_SYMLINK_HINT}")


def rule_steps(home: Path, without: list[str]) -> list[Step]:
    """A Step for each bundled rules file. The stamp file records what we wrote and
    what the user left out, so a rerun keeps both choices."""
    src = _bundled("rules")
    if src is None:
        return [Step("rules", "none bundled with this buddy")]
    dest_dir = home / ".claude" / "rules"
    stamp_path = dest_dir / STAMP
    stamp = json.loads(stamp_path.read_text()) if stamp_path.exists() else {}
    wrote, left_out = stamp.get("files", {}), set(stamp.get("without", [])) | set(without)
    steps = []
    for rule in sorted(src.glob("brahman-*.md")):
        dest = dest_dir / rule.name
        name = rule.stem
        if name in left_out:
            if dest.exists() and wrote.get(rule.name) == _digest(dest):
                steps.append(Step(f"rule {name}", "remove ours (--without)", lambda d=dest: d.unlink()))
            elif dest.exists():
                steps.append(Step(f"rule {name}", "left out (--without); your changed copy stays", None))
            else:
                steps.append(Step(f"rule {name}", "left out (--without)", None))
            continue
        want = _digest(rule)
        if not dest.exists():
            steps.append(Step(f"rule {name}", "write (new)", lambda r=rule, d=dest: _write_rule(r, d, without)))
        elif _digest(dest) == want:
            steps.append(Step(f"rule {name}", "up to date", None))
        elif wrote.get(rule.name) == _digest(dest):
            steps.append(Step(f"rule {name}", "update ours", lambda r=rule, d=dest: _write_rule(r, d, without)))
        else:
            steps.append(Step(f"rule {name}", "changed by you: left alone", None))
    if set(without) - set(stamp.get("without", [])):
        steps.append(Step("rules", f"remember --without {' '.join(sorted(set(without)))}",
                      lambda d=dest_dir: _remember_without(d, without)))
    return steps


def _write_rule(rule: Path, dest: Path, without: list[str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(rule, dest)
    stamp_path = dest.parent / STAMP
    stamp = json.loads(stamp_path.read_text()) if stamp_path.exists() else {}
    stamp.setdefault("files", {})[rule.name] = _digest(dest)
    stamp["by"] = f"brahman-buddy {VERSION}"
    stamp_path.write_text(json.dumps(stamp, indent=1) + "\n")


def _remember_without(dest_dir: Path, without: list[str]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp_path = dest_dir / STAMP
    stamp = json.loads(stamp_path.read_text()) if stamp_path.exists() else {}
    stamp["without"] = sorted(set(stamp.get("without", [])) | set(without))
    stamp_path.write_text(json.dumps(stamp, indent=1) + "\n")


def pys_steps(skip: bool) -> list[Step]:
    """pys is only looked for: its install and its own `pys setup` are the user's (a released
    pys or a work in progress from a checkout, setup can't tell and must not replace either)."""
    if skip:
        return [Step("pys", "skipped (--no-pys)")]
    pys, uv = shutil.which("pys"), shutil.which("uv")
    if pys:
        return [Step("pys", f"found ({pys}); its own setup is yours to run: pys setup (a dry run)")]
    if uv:
        out = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True, text=True).stdout.strip()
        cand = Path(out) / ("pys.exe" if os.name == "nt" else "pys") if out else None
        if cand and cand.exists():
            return [Step("pys", f"installed ({cand}) but not on PATH", None,
                         "run uv tool update-shell, open a new terminal, then pys setup --yes")]
        how = f"uv tool install {PYS_PACKAGE}, then pys setup --yes"
    else:
        how = f"its own bundle (its Python inside): {PYS_BUNDLES}, or uv, then uv tool install {PYS_PACKAGE}"
    return [Step("pys", "not found: not installed by buddy setup", None,
                 f"pys, the terminal multiplexer the buddy's sessions run in, is installed on its own: {how}")]


def _warn(text: str) -> str:
    """Yellow on a terminal that shows colour; plain in a pipe, a log, or with NO_COLOR."""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    return f"\033[1;33m{text}\033[0m"


def setup(args) -> int:
    home = Path(os.path.expanduser("~"))
    without = [w.removesuffix(".md") for w in (args.without or [])]
    steps = skill_steps(home) + rule_steps(home, without) + pys_steps(args.no_pys)
    todo = [s for s in steps if s.do is not None]
    warned = [s for s in steps if s.hint]
    width = max(len(s.what) for s in steps)
    print(f"buddy setup ({'doing it' if args.yes else 'a dry run: --yes does it'}), brahman-buddy {VERSION}")
    for what, state, do, hint in steps:
        if hint:
            print(_warn(f"  ⚠ {what:<{width}}  {state}") + f"\n      hint: {hint}")
        else:
            print(f"  {'→' if do else '·'} {what:<{width}}  {state}")
    if warned:
        print(_warn(f"{len(warned)} warning{'s' * (len(warned) > 1)} (⚠): left as they are; each hint says what to do"))
    if not args.yes:
        print(f"{len(todo)} to do. Run `buddy setup --yes` to do it." if todo else "nothing to do.")
        return 0
    failed = []
    for what, _, do, _ in todo:
        try:
            if do() is False:
                failed.append(what)
        except OSError as e:
            failed.append(f"{what}: {e}")
    print(f"done: {len(todo) - len(failed)} of {len(todo)}" + "".join(f"\n  ✗ {f}" for f in failed))
    print("next: buddy init (if not yet), then buddy doctor")
    return 1 if failed else 0
