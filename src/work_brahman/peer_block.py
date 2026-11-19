"""The peers as instructions for the agents (the owner, 2026-11-12: "allow injection into the global
instructions, off by default, which setup can turn on, with all the peers' info except the secret";
2026-11-19: "I prefer no more rules in CLAUDE.md").

Off unless peers.toml says `instructions = true` (`buddy peer setup --instructions`, buddy tui →
Peers → i, or the Setup tab). While on, every write of peers.toml and every doctor run rewrites it,
so it follows the file; turned off, it is taken out.
- **Claude Code:** its own rules file, `~/.claude/rules/brahman-peers.md` (a stamped first line),
  which Claude Code loads for every session; removed when off (only when it is ours: the stamp).
  No code here touches anyone's CLAUDE.md.
- **Codex** has no rules folder: a block between `brahman-peers:begin` and `brahman-peers:end` in
  its AGENTS.md when Codex is installed (its folder exists). Nothing outside the markers is
  changed; a copy of the file is kept beside it before a change (<name>.brahman-backup-<time>).

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
STAMP = ("<!-- brahman-peers: written by `buddy peer setup` from peers.toml (instructions = true); `buddy peer setup "
         "--off` removes it; this machine's own view, never copy it into a template or a repo -->")
BLOCK_RE = re.compile(r"<!-- brahman-peers:begin.*?<!-- brahman-peers:end -->\n?", re.S)
ALLOW_SAYS = {"local": "only this machine and SSH tunnels may connect",
              "private": "this machine, LAN and VPN addresses may connect",
              "public": "anyone may connect (plain HTTP)"}


def _where(env: Mapping[str, str], key: str, default: Path) -> Path:
    v = env.get(key) or os.environ.get(key)  # a passed env without it falls back to the process's
    return Path(v) if v else default


def rule_file(env: Mapping[str, str] = os.environ) -> Path:
    """Claude Code's: a rules file of its own, never CLAUDE.md."""
    return _where(env, "CLAUDE_CONFIG_DIR", Path.home() / ".claude") / "rules" / "brahman-peers.md"


def files(env: Mapping[str, str] = os.environ) -> list[Path]:
    """Where it goes: Claude Code's rules file always, Codex's AGENTS.md (a block) when Codex is there."""
    codex = _where(env, "CODEX_HOME", Path.home() / ".codex")
    return [rule_file(env)] + ([codex / "AGENTS.md"] if codex.is_dir() else [])


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


def _sync_rule(f: Path, block: str | None) -> str:
    """The rules file: the whole file is ours (the stamp), written when on, removed when off."""
    old = f.read_text(encoding="utf-8") if f.exists() else ""
    if old and not old.startswith(STAMP):
        return f"left alone {f}: not written by buddy peer (no stamp)"
    if block is None:
        if not old:
            return ""
        f.unlink()
        return f"removed {f}"
    body = block.replace(BEGIN + "\n", "").replace("\n" + END, "")
    new = STAMP + "\n\n" + body
    if _same(new, old):
        return ""
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.tmp")  # whole or not at all: a file cut mid-stamp would never be ours again
    tmp.write_text(new, encoding="utf-8")
    tmp.chmod(0o600)  # it names the peers' hosts and ports
    os.replace(tmp, f)
    return f"{'updated' if old else 'wrote'} {f}"


def sync(cfg: dict, env: Mapping[str, str] = os.environ) -> list[str]:
    """Bring it in line with cfg["instructions"]: what changed, one line per file."""
    from . import peer as P
    block = render(cfg, P.seen(env)) if cfg.get("instructions") else None
    said = []
    rule = rule_file(env)
    for f in files(env):
        if f == rule:
            if (line := _sync_rule(f, block)):
                said.append(line)
            continue
        old = f.read_text(encoding="utf-8") if f.exists() else ""
        if block is None and not BLOCK_RE.search(old):
            continue  # off and not there: the file is not touched, not even created
        new = new_text(old, block)
        if _same(new, old):
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists():
            shutil.copy2(f, f.with_name(f"{f.name}.brahman-backup-{time.strftime('%Y%m%d-%H%M%S')}"))
        f.write_text(new, encoding="utf-8")
        said.append(f"{'updated' if block and BLOCK_RE.search(old) else 'added' if block else 'removed'} the peers block "
                    f"in {f}")
    return said


def state(env: Mapping[str, str] = os.environ) -> list[dict]:
    """Each file and whether it carries the peers, for the TUI and the page."""
    rule = rule_file(env)
    return [{"file": str(f), "has": f.exists() and (f.read_text(encoding="utf-8").startswith(STAMP) if f == rule
                                                     else bool(BLOCK_RE.search(f.read_text(encoding="utf-8"))))}
            for f in files(env)]
