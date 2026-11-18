"""Which of the owner's own tools a story's step used (specs/views.md, phase 2; the owner, 2026-11-14:
"the user's side of tools and skills is what we really care about"). One PostToolUse hook, a script
by rules, $0: no model.

- **The user's side only.** An MCP server he connected (`mcp__<server>__<tool>` → the server), a skill
  (the `Skill` tool → its name; a Bash call of a script under `…/skills/<name>/` → `<name>: <file>`),
  his own CLIs (a Bash call whose first program lives under the home folder, or is on the short list:
  `gh`, `git`, `systemctl` …), the web (WebSearch, WebFetch). The engine's built-ins (Read, Edit, Grep,
  a coreutil in Bash …) are its own hands, not the system's architecture: nothing is recorded.
- **Where it goes.** The name is added once to the step that session is doing (a mark's `tools`), found
  by the session's id in the step's `by` (executor()). A session that never marked a running story is
  not looked at: `short_stories/attached/<id>` gates the hook (scripts/buddy-short).
- **The arguments** (his choice: "a copy is better"): copied apart from the story, one JSON line per call
  in `args/<story>.jsonl` under the state folder, each value cut to ARG_MAX; private (0600), never in
  the story's events, so peer sync never carries them; read only behind ⚙ "debug: arguments".
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

ARG_MAX = 4096
CLI = ("gh", "git", "glab", "systemctl", "docker", "podman", "ssh", "scp", "rsync", "uv", "uvx", "pys",
       "buddy", "buddy-short", "buddy-long", "buddy-team", "samvada", "privacy-scan", "heads", "gitctx", "lark-cli",
       "curl", "kubectl", "terraform", "aws", "gcloud", "az")
SYSTEMS = {"gh": "GitHub", "glab": "GitLab", "lark-cli": "Lark", "aws": "AWS", "gcloud": "Google Cloud",
           "az": "Azure", "kubectl": "Kubernetes", "docker": "Docker", "podman": "Podman"}
WRAPPERS = {"sudo", "env", "time", "nice", "nohup", "exec", "command", "timeout"}


def settings(cfg: dict | None) -> tuple[tuple, dict]:
    """[views] cli (programs that count beside the home folder's own) and systems (program → its system)."""
    v = (cfg or {}).get("views") or {}
    return tuple(v.get("cli") or CLI), {**SYSTEMS, **(v.get("systems") or {})}


def first_program(command: str) -> str:
    """The first program a shell command runs: past `cd …&&` or a `cd` line, `X=1`, sudo/env/timeout and
    their flags; split as bash does (newlines, quotes, here-documents: effects._split); "" when unsure."""
    from .effects import _split, _words
    for seg in _split(command or ""):
        if words := _words(seg):
            return words[0]
    return ""


def classify(tool: str, inp: dict, home: Path, cli: tuple = CLI, systems: dict | None = None,
             which=None) -> str | None:
    """The user-side name of one tool call as "<kind>:<name>" (kind: mcp, skill, cli, web; the System
    view groups by it), or None for the engine's built-ins."""
    systems = SYSTEMS if systems is None else systems
    if tool.startswith("mcp__"):
        return f"mcp:{tool.split('__')[1]}" if tool.split("__")[1] else None
    if tool == "Skill":
        return f"skill:{inp.get('skill') or inp.get('name') or 'skill'}"
    if tool in ("WebSearch", "WebFetch"):
        return "web:web"
    if tool != "Bash":
        return None
    command = str(inp.get("command", ""))
    first = re.split(r"&&|\|\||;|\|", command)
    run = next((x for x in first if first_program(x)), "")  # the segment that runs something
    if m := re.search(r"/skills/([^/\s]+)/(?:\S*/)?([^/\s]+?)(?=\s|$)", run):  # a skill's script, run directly or by bash/python
        return f"skill:{m.group(1)}: {m.group(2)}"
    prog = first_program(command)
    if not prog:
        return None
    name = os.path.basename(prog)
    if name in cli:
        return f"cli:{name} ({systems[name]})" if name in systems else f"cli:{name}"
    import shutil
    path = prog if "/" in prog else (which or shutil.which)(prog)
    if path:
        try:
            Path(path).resolve().relative_to(home)
            return f"cli:{name} ({systems[name]})" if name in systems else f"cli:{name}"
        except (ValueError, OSError):
            pass
    return None


def gate(state: Path, session_id: str) -> Path:
    return Path(state) / "short_stories" / "attached" / session_id[:8]


def attach(state: Path, by: str) -> None:
    """A mark from a Claude session: from now on its hook looks for the step it is doing."""
    if m := re.search(r":claude-([0-9a-f]{8})", by or ""):
        p = gate(state, m.group(1))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


def step_of(state: Path, session_id: str) -> tuple[int, str] | None:
    """(story, step id) this session is doing now: its latest open "doing" in a running story."""
    from . import short_stories as shorts
    tag = f":claude-{session_id[:8]}"
    best = None
    for s in shorts.all_stories(state):
        if s.get("status") != "running":
            continue
        last: dict[str, dict] = {}
        for e in s.get("events", []):
            if e.get("step"):
                last[e["step"]] = e
        for sid, e in last.items():
            if e.get("status") == "doing" and tag in (e.get("by") or "") and (best is None or e["at"] > best[2]):
                best = (s["id"], sid, e["at"])
    return (best[0], best[1]) if best else None


def _cut(v):
    if isinstance(v, str):
        return v if len(v) <= ARG_MAX else v[:ARG_MAX] + f"… ({len(v)} chars)"
    if isinstance(v, dict):
        return {k: _cut(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_cut(x) for x in v[:50]]
    return v


def args_file(state: Path, story: int) -> Path:
    return Path(state) / "args" / f"{story}.jsonl"


def record(state: Path, story: int, step: str, name: str | None, tool: str, inp: dict, call_id: str = "",
           effects: list[dict] | None = None) -> None:
    """The name onto the step (once), its effects counted by kind and what (phase 3), the arguments
    into the story's own args file."""
    from . import short_stories as shorts
    with shorts._locked(state, str(story)):
        s = shorts.load(state, story)
        m = next((m for m in (s or {}).get("marks", []) if m.get("id") == step), None)
        changed = False
        if m is not None and name and name not in m.setdefault("tools", []):
            m["tools"].append(name)
            changed = True
        for e in effects or []:
            if m is None:
                break
            have = next((x for x in m.setdefault("effects", []) if (x["kind"], x["what"]) == (e["kind"], e["what"])), None)
            if have:
                have["n"] = have.get("n", 1) + 1
            else:
                m["effects"].append({**e, "n": 1, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            changed = True
        if changed:
            shorts._save(state, s)
    f = args_file(state, story)
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(f.parent, 0o700)  # the folder too: no window between a create with the umask and a chmod (review)
    fd = os.open(f, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as out:
        out.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "step": step, "name": name, "tool": tool,
                              "id": call_id, "input": _cut(inp)}, ensure_ascii=False) + "\n")


def hook(state: Path, event: dict, cfg: dict | None = None) -> str:
    """One PostToolUse event; returns what it did (for tests and the log), never raises."""
    try:
        sid, tool = str(event.get("session_id", "")), str(event.get("tool_name", ""))
        if not sid or not gate(state, sid).exists():
            return "not attached"
        cli, systems = settings(cfg)
        from . import effects as E
        name = classify(tool, event.get("tool_input") or {}, Path.home(), cli, systems)
        effs = E.of_call(tool, event.get("tool_input") or {})
        if name is None and not effs:
            return "built-in"
        where = step_of(state, sid)
        if where is None:
            gate(state, sid).unlink(missing_ok=True)  # no running story's step: stop looking
            return "no step"
        record(state, where[0], where[1], name, tool, event.get("tool_input") or {}, str(event.get("tool_use_id", "")), effs)
        return f"#{where[0]} {where[1]}: {name or ''}" + "".join(f" +{e['kind']}" for e in effs)
    except Exception as e:  # noqa: BLE001 - a hook never breaks the session
        return f"failed: {type(e).__name__}"


def read_args(state: Path, story: int, step: str | None = None) -> list[dict]:
    f = args_file(state, story)
    if not f.exists():
        return []
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return [r for r in rows if step is None or r.get("step") == step]


# ---- install into Claude Code's settings (the owner's file: a dated copy first) -----------------

MATCHER = "Bash|Skill|WebSearch|WebFetch|mcp__.*"   # the engine never calls it for its other built-ins
COMMAND = "buddy-short tool-hook"


def install(settings_path: Path, remove: bool = False) -> str:
    d = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = d.setdefault("hooks", {})
    post = [h for h in hooks.get("PostToolUse", []) if not any(x.get("command") == COMMAND for x in h.get("hooks", []))]
    had = len(post) != len(hooks.get("PostToolUse", []))
    if not remove:
        post.append({"matcher": MATCHER, "hooks": [{"type": "command", "command": COMMAND, "timeout": 10}]})
    if post:
        hooks["PostToolUse"] = post
    else:
        hooks.pop("PostToolUse", None)
    if remove and not had:
        return f"no tool hook in {settings_path}"
    if settings_path.exists():  # a dated copy, never over an earlier one (two changes in one second)
        import shutil
        stamp, k = time.strftime("%Y%m%d-%H%M%S"), 0
        while (copy := settings_path.with_name(f"{settings_path.name}.before-tool-hook-{stamp}" + (f".{k}" if k else ""))).exists():
            k += 1
        shutil.copy2(settings_path, copy)
    settings_path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    return f"{'removed the' if remove else 'updated the' if had else 'added the'} tool hook {'from' if remove else 'in'} {settings_path}"
