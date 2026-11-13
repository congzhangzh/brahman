"""What's new (the owner, 2026-11-12: "a release note and a tab on the web, and post it to the chats
once when a new version is released").

CHANGES.md (beside this file, so it ships in the wheel) has one section per release, newest first,
written at each release. The workbench's What's new tab shows it; at the first start of a new
version `buddy serve` posts the top released section to the owner's chats once (announce_once),
like any post, so it follows [notify]: with the defaults (every app off) only the tab shows it.
A checkout (version 0.1.0, not a release) never announces.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV = "0.1.0"  # pyproject's version in a checkout; a release sets its own


def version() -> str:
    try:
        from importlib.metadata import version as v
        return v("brahman-buddy")
    except Exception:  # noqa: BLE001 - not installed as a package
        return DEV


def commit() -> tuple[str, bool]:
    """(the source commit, a checkout?): a release's from _build.json (release.sh writes it beside
    this file), a checkout's from git; ("", False) when neither says."""
    try:
        return json.loads((HERE / "_build.json").read_text()).get("commit", ""), False
    except (OSError, ValueError):
        pass
    import subprocess
    try:
        got = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return (got.stdout.strip(), True) if got.returncode == 0 else ("", False)
    except (OSError, subprocess.SubprocessError):
        return "", False


def text() -> str:
    try:
        return (HERE / "CHANGES.md").read_text()
    except OSError:
        return ""


def sections(md: str | None = None) -> list[tuple[str, str]]:
    """[(heading, body)] in file order: "Unreleased", "20261112.7", …"""
    md = text() if md is None else md
    out = []
    for part in re.split(r"(?m)^## ", md)[1:]:
        head, _, body = part.partition("\n")
        out.append((head.strip(), body.strip()))
    return out


def released(md: str | None = None) -> tuple[str, str] | None:
    """The newest section that is a release (not "Unreleased")."""
    return next(((h, b) for h, b in sections(md) if re.fullmatch(r"\d{8}\.\d+(\.dev\d+)?", h)), None)


def _seen_path(state: Path) -> Path:
    return state / "version-seen.json"


def announce_once(state: Path, channels: list, chats: list[str], ver: str | None = None, post_all=None) -> str:
    """At serve's start: the first time this version runs, post its section to the chats; returns what
    happened ("" when nothing). The version is remembered, so never twice."""
    ver = ver or version()
    if ver == DEV:
        return ""
    p = _seen_path(state)
    try:
        seen = json.loads(p.read_text()).get("version", "")
    except (OSError, ValueError):
        seen = ""
    if seen == ver:
        return ""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": ver}))
    sec = next(((h, b) for h, b in sections() if h == ver), None)
    if not seen or sec is None or not chats:  # a first install: nothing to announce; or no notes for it
        return "remembered"
    if post_all is None:
        from .channel import post_all
    body = sec[1] if len(sec[1]) < 3000 else sec[1][:3000] + "\n\n…"
    post_all(channels, chats, body + "\n\n(the workbench's What's new tab has it all)",
             title=f"brahman-buddy {ver} · what's new")
    return "posted"
