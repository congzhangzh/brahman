"""The peers as a block in the global instructions (the owner, 2026-11-12: "allow injection into
the global instructions, off by default, which setup can turn on, with all the peers' info
except the secret").

Off unless peers.toml says `instructions = true` (`buddy peer setup --instructions`, buddy tui →
Peers → i, or the Setup tab). While on, every write of peers.toml and every doctor run rewrites
the block, so it follows the file; turned off, the block is taken out. It sits between
`brahman-peers:begin` and `brahman-peers:end` in Claude Code's CLAUDE.md, and in Codex's
AGENTS.md when Codex is installed (its folder exists). Nothing outside the markers is changed;
a copy of the file is kept beside it before a change (<name>.brahman-backup-<time>).

What goes in: everything in peers.toml (this side, its description and allow, each peer's
address, direction and note, each resource) and what each peer last said of itself; never the
secret, which is not in that file. The block is this machine's own view, so it says not to copy
it into a template or a repo. A session reads it when it starts: `buddy doctor --peers` is the
live truth.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

BEGIN = ("<!-- brahman-peers:begin (managed by `buddy peer`: peers.toml as instructions; turn it off with "
         "`buddy peer setup --off`; this machine's own view, never copy it into a template or a repo) -->")
END = "<!-- brahman-peers:end -->"
BLOCK_RE = re.compile(r"<!-- brahman-peers:begin.*?<!-- brahman-peers:end -->\n?", re.S)
ALLOW_SAYS = {"local": "only this machine and SSH tunnels may connect",
              "private": "this machine, LAN and VPN addresses may connect",
              "public": "anyone may connect (plain HTTP)"}


def files(env: Mapping[str, str] = os.environ) -> list[Path]:
    """Where the block goes: Claude Code's CLAUDE.md always, Codex's AGENTS.md when Codex is there."""
    def where(key: str, default: Path) -> Path:  # a passed env without it falls back to the process's
        v = env.get(key) or os.environ.get(key)
        return Path(v) if v else default
    claude, codex = where("CLAUDE_CONFIG_DIR", Path.home() / ".claude"), where("CODEX_HOME", Path.home() / ".codex")
    return [claude / "CLAUDE.md"] + ([codex / "AGENTS.md"] if codex.is_dir() else [])


def _cell(s: str) -> str:
    return (s or "").replace("|", "/").replace("\n", " ") or "–"


def render(cfg: dict, seen: dict) -> str:
    from . import peer as P
    lines = [BEGIN, "## Peers of this machine (buddy peer)", "",
             f"This machine is **{cfg['name']}** (machine {P.fingerprint()})"
             + (f": {cfg['description']}" if cfg.get("description") else "") + ".",
             f"Who may connect: `allow = {cfg.get('allow', 'local')}`, {ALLOW_SAYS[cfg.get('allow', 'local')]}.", ""]
    if cfg["points"]:
        lines += ["| Peer | Address from here | Direction | What it says of itself | Owner's note |", "|---|---|---|---|---|"]
        for k in sorted(cfg["points"]):
            v, s = cfg["points"][k], seen.get(k, {})
            lines.append(f"| {k} | {_cell(v['url'])} | {'send-only (it reaches us)' if v.get('direction') == 'in' else 'both ways'}"
                         f" | {_cell(s.get('desc', ''))} | {_cell(v.get('note', ''))} |")
    else:
        lines.append("No peers yet.")
    res = cfg.get("resources") or {}
    if res:
        lines += ["", "Resources: name them in steps and notes, never their address; each machine maps a name to its own "
                      "way there.", "", "| Resource | Kind | How this machine reaches it | Note | Peers that have it |",
                  "|---|---|---|---|---|"]
        for k in sorted(res):
            v = res[k]
            have = [p for p in sorted(seen) if p in cfg["points"] and k in seen[p].get("res", [])]
            lines.append(f"| {k} | {v['kind']} | {_cell(v['url'])} | {_cell(v.get('note', ''))} | {_cell(', '.join(have))} |")
    lines += ["", "Work across machines: `buddy peer story start \"<title>\" --step build --step test@<peer>` "
                  "(one owner machine per step), `buddy peer story mark <story> <step>`, `buddy peer story sync`; "
                  "code travels as `buddy peer send <peer> <repo> <range> --note …`. "
                  "`buddy doctor --peers` is the live truth (this block is as of "
                  f"{time.strftime('%Y-%m-%d %H:%M')}). The secret is BRAHMAN_PEER_SECRET in the environment: "
                  "never write it down or show it.", END]
    return "\n".join(lines) + "\n"


def new_text(old: str, block: str | None) -> str:
    """The file with the block put in (in place, else at the end), or taken out (block None)."""
    if block is None:
        s = re.sub(r"\n*" + BLOCK_RE.pattern, "\n", old, count=1, flags=re.S)
        return s.lstrip("\n") if old.startswith("<!-- brahman-peers:begin") else s
    if BLOCK_RE.search(old):
        return BLOCK_RE.sub(lambda _: block, old, count=1)
    return (old.rstrip("\n") + "\n\n" if old.strip() else "") + block


def _same(a: str, b: str) -> bool:
    """Equal but for the block's own 'as of' time: no rewrite (and no backup) for that alone."""
    strip = lambda t: re.sub(r"this block is as of [0-9-]+ [0-9:]+", "", t)
    return strip(a) == strip(b)


def sync(cfg: dict, env: Mapping[str, str] = os.environ) -> list[str]:
    """Bring the block in line with cfg["instructions"]: what changed, one line per file."""
    from . import peer as P
    block = render(cfg, P.seen(env)) if cfg.get("instructions") else None
    said = []
    for f in files(env):
        old = f.read_text() if f.exists() else ""
        if block is None and not BLOCK_RE.search(old):
            continue  # off and not there: the file is not touched, not even created
        new = new_text(old, block)
        if _same(new, old):
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists():
            shutil.copy2(f, f.with_name(f"{f.name}.brahman-backup-{time.strftime('%Y%m%d-%H%M%S')}"))
        f.write_text(new)
        said.append(f"{'updated' if block and BLOCK_RE.search(old) else 'added' if block else 'removed'} the peers block "
                    f"in {f}")
    return said


def state(env: Mapping[str, str] = os.environ) -> list[dict]:
    """Each file and whether the block is in it, for the TUI and the page."""
    return [{"file": str(f), "has": f.exists() and bool(BLOCK_RE.search(f.read_text()))} for f in files(env)]
