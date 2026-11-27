"""`buddy-team` and `buddy-short`: the team's and the short stories' commands as entry points of the package, so a
release has them on every system, Windows too (the owner, 2026-11-26: "the brahman team is not released to strip,
which is very important"). The same as `buddy team …` and `buddy short …`, with the same arguments; BUDDY_TOML
picks another buddy.toml, as the scripts in a checkout did (scripts/buddy-team, scripts/buddy-short).
"""

from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path


def _buddy(cmd: str) -> int:
    cfg = os.environ.get("BUDDY_TOML")
    sys.argv = ["buddy", *(["-c", cfg] if cfg else []), cmd, *sys.argv[1:]]
    from .cli import main
    return main()


def team() -> int:
    return _buddy("team")


def short() -> int:
    if sys.argv[1:2] == ["tool-hook"]:
        # Claude Code's PostToolUse (tooluse.py) on every tool call: a session that never attached a story
        # ends here, with the standard library only, before the buddy is imported; an attached one goes on,
        # with the event on stdin.
        event = sys.stdin.read()
        m = re.search(r'"session_id"\s*:\s*"([0-9a-f]{8})', event)
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "work_brahman"
        if not m or not (state / "short_stories" / "attached" / m[1]).exists():
            return 0
        sys.stdin = io.StringIO(event)
    return _buddy("short")
