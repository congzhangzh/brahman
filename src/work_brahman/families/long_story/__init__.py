"""Family: long story. One story per agent session, mirrored to a thread in the owner's
chats, so he can follow it and talk to it anywhere. See FAMILY.md; the board itself is
long_stories.py, written by the session's hooks (`buddy long hook`) and by `buddy serve`."""

import os
import time
from pathlib import Path

from ... import asks, long_stories as longs
from ...text import t

LOCAL = "%Y-%m-%dT%H:%M:%S"  # the stamp without its offset: written on this machine


class LongStory:
    name = "long-story"

    def __init__(self, state_dir: Path, cfg: dict | None = None):
        self.state_dir = state_dir
        self.channels, self.engines, self.linked, self.workspace = [], {}, None, "~"

    def connect(self, channels: list, engines: dict, linked, workspace: str) -> None:
        """`buddy serve` hands over what /long new needs: the chats and the engines, by kind
        (engine.long_engines: [long] in buddy.toml says how much they may do)."""
        self.channels, self.engines, self.linked, self.workspace = channels, engines, linked, workspace

    def commands(self) -> dict:
        return {"/long": (t("long.help"), self.markdown)}

    def stories(self) -> list[dict]:
        return longs.all_stories(self.state_dir)

    def workflow(self) -> dict:
        """Its row in the workbench's level 1: the family is the workflow."""
        return {"kind": "family", "name": self.name, "schedule": "", "about": t("long.about")}

    def runs(self, days: int = 3) -> list[dict]:
        """Its stories as level 1 runs, newest first: those a terminal holds, and those that
        did something in the last `days`. Shaped like a run record (runs.py); its steps are
        the story's own log (terminal, say, turn, wait, away)."""
        since = time.time() - days * 86400
        out = []
        for s in reversed(longs.all_stories(self.state_dir)):
            events = s.get("events", [])
            last = events[-1]["at"] if events else s["started"]
            if not longs.held(s) and time.mktime(time.strptime(last[:19], LOCAL)) < since:
                continue
            t0 = time.mktime(time.strptime(s["started"][:19], LOCAL))
            out.append({
                "id": f"{self.name}:{s['id']}", "kind": "family", "name": self.name,
                "trigger": f"{longs.label(s)} · {s.get('engine', 'claude-code')} · {s['cwd']}",
                "status": "running" if longs.held(s) else "ok",
                "started": s["started"], "ended": None if longs.held(s) else last, "version": "",
                "steps": [{k: v for k, v in e.items() if k != "text"} | {"note": e["text"]} for e in events],
                "result": events[-1]["text"] if events else "",
                "fitness": {"seconds": int(time.time() - t0)}})
        return out

    def markdown(self, arg: str = "") -> str:
        """/long: the board, nothing more. /long new: a card to start one (engine, folder, then
        the task). /long new [claude|codex] [folder] <task>: start one in a line."""
        word, _, rest = arg.strip().partition(" ")
        if word == "help":
            return t("long.usage")
        if word == "new" and rest.strip() and self.engines and self.linked:
            kind, cwd, task = self._parse(rest.strip())
            try:
                s = longs.new(self.state_dir, self.channels, self.linked.chats, task, cwd=cwd, where="chat",
                              engine=self.engines[kind], kind=kind)
            except ValueError as e:
                return str(e)
            return longs.label(s)
        if word == "new" and self.linked and self.channels:
            asks.create(self.state_dir, self.channels, self.linked.chats, t("long.pick_engine"),
                        title=t("long.title.start"), options=[["claude", "Claude"], ["codex", "Codex"], ["cancel", t("btn.cancel")]],
                        then={"claude": ["long", "engine", "claude-code"], "codex": ["long", "engine", "codex"]})
            return t("long.carded")
        if not longs.all_stories(self.state_dir):
            return longs.board(self.state_dir)  # long.none says how to start one already
        return longs.board(self.state_dir) + "\n\n" + t("long.start_hint")

    def _parse(self, text: str) -> tuple[str, str, str]:
        """[claude|codex] [folder] task: the engine and folder if given, else Claude and the workspace."""
        kind, cwd = "claude-code", os.path.expanduser(self.workspace)
        word, _, rest = text.partition(" ")
        if word.lower() in ("claude", "codex") and rest.strip():
            kind, text = ("codex" if word.lower() == "codex" else "claude-code"), rest.strip()
            word, _, rest = text.partition(" ")
        if word.startswith(("~", "/")) and os.path.isdir(os.path.expanduser(word)) and rest.strip():
            cwd, text = os.path.realpath(os.path.expanduser(word)), rest.strip()
        return kind, cwd, text
