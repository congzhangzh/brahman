"""`buddy setup`: the personal engineering platform on this machine (specs/brahman-setup.md).

A dry run by default: it says what it would do; `--yes` does it. Run it again any time: it
brings what is ours up to date and leaves alone what is the user's.

- skills: the buddy's agent skills in ~/.claude/skills. An installed package links them (they
  follow every upgrade); the one-file executable unpacks into a folder that goes away when it
  exits, so it copies them, with a stamp recording what it wrote. A same-named skill that is
  neither our link nor our untouched copy is the user's: left alone and named.
- rules: the working rules as drop-ins in ~/.claude/rules/brahman-*.md, which Claude Code loads
  for every session. A file the user changed is theirs from then on; `--without <name>` leaves
  one out (and keeps it out). The user's own CLAUDE.md is read after them, so a line there wins.
- pys: the terminal multiplexer, its own package with its own setup. Installed as a uv tool
  (the buddy never imports it, and it needs Python 3.14 whatever the buddy runs on), then
  `pys setup --yes`. With no uv, setup says where pys's own bundle is.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .runs import VERSION

HERE = Path(__file__).resolve().parent
PYS_SPEC = "py-screen-mux>=20261109.2"  # brings `pys setup` and its config.d drop-in
PYS_BUNDLES = "https://github.com/congzhangzh/py-screen/releases/latest"
STAMP = ".brahman-buddy.json"  # in a skill we copied, and once in ~/.claude/rules: what we wrote


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


def skill_steps(home: Path) -> list[tuple[str, str, object]]:
    """(what, state, do) for each bundled skill; do is None when there is nothing to do."""
    src = _bundled("skills")
    if src is None:
        return [("skills", "none bundled with this buddy", None)]
    dest_dir = home / ".claude" / "skills"
    steps = []
    for skill in sorted(p for p in src.iterdir() if (p / "SKILL.md").exists()):
        dest = dest_dir / skill.name
        want = _digest(skill)
        stamp = dest / STAMP
        if _frozen():  # the one-file's folder goes away when it exits: copy, with a stamp
            if not dest.exists() and not dest.is_symlink():
                steps.append((f"skill {skill.name}", "copy (new)", lambda s=skill, d=dest, w=want: _copy(s, d, w)))
            elif stamp.exists() and json.loads(stamp.read_text()).get("sha256") == _digest(dest):
                if _digest(dest) == want:
                    steps.append((f"skill {skill.name}", "up to date", None))
                else:
                    steps.append((f"skill {skill.name}", "update our copy", lambda s=skill, d=dest, w=want: _copy(s, d, w)))
            elif _ours_link(dest, "skills"):
                steps.append((f"skill {skill.name}", "replace our old link with a copy",
                               lambda s=skill, d=dest, w=want: _copy(s, d, w)))
            else:
                steps.append((f"skill {skill.name}", "yours (changed or not ours): left alone", None))
            continue
        if dest.is_symlink() and _same(dest, skill):
            steps.append((f"skill {skill.name}", "up to date (linked)", None))
        elif not dest.exists() and not dest.is_symlink():
            steps.append((f"skill {skill.name}", "link (new)", lambda s=skill, d=dest: _link(s, d)))
        elif _ours_link(dest, "skills"):
            steps.append((f"skill {skill.name}", "relink (an older or gone install of ours)",
                           lambda s=skill, d=dest: _link(s, d)))
        elif stamp.exists() and json.loads(stamp.read_text()).get("sha256") == _digest(dest):
            if _digest(dest) == want:  # our copy, current: where symlinks are not allowed (Windows
                steps.append((f"skill {skill.name}", "up to date (copied)", None))  # without Developer Mode)
            else:  # our copy, older: a link if this system allows one, else a fresh copy
                steps.append((f"skill {skill.name}", "update our copy", lambda s=skill, d=dest: _link(s, d)))
        else:
            steps.append((f"skill {skill.name}", "yours (changed or not ours): left alone", None))
    return steps


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
    except OSError:  # Windows without Developer Mode can't make symlinks: a copy then
        shutil.copytree(src, dest)
        (dest / STAMP).write_text(json.dumps({"by": f"brahman-buddy {VERSION}", "sha256": _digest(src)}) + "\n")


def rule_steps(home: Path, without: list[str]) -> list[tuple[str, str, object]]:
    """(what, state, do) for each bundled rules file. The stamp file records what we wrote and
    what the user left out, so a rerun keeps both choices."""
    src = _bundled("rules")
    if src is None:
        return [("rules", "none bundled with this buddy", None)]
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
                steps.append((f"rule {name}", "remove ours (--without)", lambda d=dest: d.unlink()))
            elif dest.exists():
                steps.append((f"rule {name}", "left out (--without); your changed copy stays", None))
            else:
                steps.append((f"rule {name}", "left out (--without)", None))
            continue
        want = _digest(rule)
        if not dest.exists():
            steps.append((f"rule {name}", "write (new)", lambda r=rule, d=dest: _write_rule(r, d, without)))
        elif _digest(dest) == want:
            steps.append((f"rule {name}", "up to date", None))
        elif wrote.get(rule.name) == _digest(dest):
            steps.append((f"rule {name}", "update ours", lambda r=rule, d=dest: _write_rule(r, d, without)))
        else:
            steps.append((f"rule {name}", "changed by you: left alone", None))
    if set(without) - set(stamp.get("without", [])):
        steps.append(("rules", f"remember --without {' '.join(sorted(set(without)))}",
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


def pys_steps(skip: bool) -> list[tuple[str, str, object]]:
    if skip:
        return [("pys", "skipped (--no-pys)", None)]
    pys, uv = shutil.which("pys"), shutil.which("uv")
    if pys:
        return [("pys", f"found ({pys}): run its own setup", lambda p=pys: _run([p, "setup", "--yes"]))]
    if uv:
        return [("pys", f"install as a uv tool ({PYS_SPEC}), then its own setup",
                 lambda u=uv: _run([u, "tool", "install", PYS_SPEC]) and _run_pys_setup(u))]
    return [("pys", f"not installed and no uv: take pys's own bundle (its Python inside): {PYS_BUNDLES}", None)]


def _run(cmd: list[str]) -> bool:
    print("  $ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode == 0


def _run_pys_setup(uv: str) -> bool:
    """uv's tool bin may not be on PATH yet: ask uv where it put pys."""
    pys = shutil.which("pys")
    if not pys:
        out = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True, text=True).stdout.strip()
        cand = Path(out) / ("pys.exe" if os.name == "nt" else "pys")
        pys = str(cand) if cand.exists() else None
    if not pys:
        print("  pys was installed but is not on PATH: run `uv tool update-shell`, then `pys setup --yes`")
        return False
    return _run([pys, "setup", "--yes"])


def setup(args) -> int:
    home = Path(os.path.expanduser("~"))
    without = [w.removesuffix(".md") for w in (args.without or [])]
    steps = skill_steps(home) + rule_steps(home, without) + pys_steps(args.no_pys)
    todo = [s for s in steps if s[2] is not None]
    width = max(len(w) for w, _, _ in steps)
    print(f"buddy setup ({'doing it' if args.yes else 'a dry run: --yes does it'}), brahman-buddy {VERSION}")
    for what, state, do in steps:
        print(f"  {'→' if do else '·'} {what:<{width}}  {state}")
    if not args.yes:
        print(f"{len(todo)} to do. Run `buddy setup --yes` to do it." if todo else "nothing to do.")
        return 0
    failed = []
    for what, _, do in todo:
        try:
            if do() is False:
                failed.append(what)
        except OSError as e:
            failed.append(f"{what}: {e}")
    print(f"done: {len(todo) - len(failed)} of {len(todo)}" + "".join(f"\n  ✗ {f}" for f in failed))
    print("next: buddy init (if not yet), then buddy doctor")
    return 1 if failed else 0
