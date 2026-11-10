"""Family: short story. One story per piece of work that waits on pipelines between its steps
(a release: wait on CI, push, wait on the build, publish, ...): it reports its marks to the owner's chats as it goes. Short next to the
stories of upstream-mr, which last weeks. See FAMILY.md; the board itself is
short_stories.py, written by `buddy short ...`."""

import time
from pathlib import Path

from ... import short_stories as shorts, teams
from ...text import t

LOCAL = "%Y-%m-%dT%H:%M:%S"  # the stamp without its offset: written on this machine


class ShortStory:
    name = "short-story"

    def __init__(self, state_dir: Path, cfg: dict | None = None):
        self.state_dir = state_dir
        self.channels = []  # set by `buddy serve`, so /short N <text> changes the cards

    def commands(self) -> dict:
        return {"/short": (t("short.help"), self.markdown)}

    def stories(self) -> list[dict]:
        return shorts.all_stories(self.state_dir)

    def workflow(self) -> dict:
        """Its row in the workbench's level 1: the family is the workflow."""
        return {"kind": "family", "name": self.name, "schedule": "",
                "about": t("short.about")}

    def runs(self, days: int | None = 3, full: int | None = None) -> list[dict]:
        """Its stories as level 1 runs, newest first: the running ones, and those started in
        the last `days` (None: every story, none is ever dropped). Shaped like a run record
        (runs.py), plus the story itself, so the workbench draws them with the duties' runs and
        shows the card when one is picked. full: past the running ones and the newest `full`,
        a story is a light tile (no card, no steps) that the page fetches whole (detail) when
        picked: 300 whole stories are 1.4 MB, and the page asks every 5 s."""
        since = time.time() - days * 86400 if days is not None else None
        out = []
        self._members = (teams.sessions_to_roles(self.state_dir), teams.team_index(self.state_dir))
        for s in reversed(shorts.all_stories(self.state_dir)):
            t0 = time.mktime(time.strptime(s["started"][:19], LOCAL))
            if since is not None and s["status"] != "running" and t0 < since:
                continue
            whole = full is None or s["status"] == "running" or len(out) < full
            out.append(self._run(s) if whole else self._tile(s))
        return out

    def detail(self, key: str) -> dict | None:
        """One story as a whole run, for the page when a light tile is picked."""
        s = shorts.load(self.state_dir, int(key)) if key.isdigit() else None
        self._members = (teams.sessions_to_roles(self.state_dir), teams.team_index(self.state_dir))
        return self._run(s) if s else None

    def _tile(self, s: dict) -> dict:
        run = self._run(s, card=False)
        run["steps"] = []
        return run

    def _run(self, s: dict, card: bool = True) -> dict:
        """A story as a run record; card=False leaves out what only its card needs."""
        t0 = time.mktime(time.strptime(s["started"][:19], LOCAL))
        t1 = time.mktime(time.strptime(s["ended"][:19], LOCAL)) if s["ended"] else time.time()
        status = {"running": "running", "done": "ok",
                  "failed": "stopped" if s.get("stop") else "failed"}[s["status"]]
        return {
            "id": f"{self.name}:{s['id']}", "kind": "family", "name": self.name,
            "trigger": t("short.started_by", who=s.get("who") or t("short.a_session")), "status": status,
            "started": s["started"], "ended": s["ended"], "version": "",
            "steps": [{"step": m["name"] + (f" → {m['fork']['took']}" if (m.get("fork") or {}).get("took") else "")
                       + shorts.rounds(m),
                       "status": m["status"], "at": m.get("at"),
                       "note": m.get("note", ""), "planned": m.get("planned", True)}
                      for m in s["marks"]],
            "result": s["events"][-1]["text"] if s["events"] else "",
            "fitness": {"seconds": int(t1 - t0)},
            "story": {"id": s["id"], "title": s["title"], "who": s.get("who", ""),
                      "markdown": shorts.markdown(s), "buttons": shorts.buttons(s),
                      "stop": s.get("stop"), "replies": s.get("replies", []),
                      "questions": s.get("questions", []),
                      "parent": s.get("parent"), "children": shorts.children(s),
                      "events": s["events"]} if card else
                     {"id": s["id"], "title": s["title"], "parent": s.get("parent"), "lite": True},
            # its team member, for the page's link to the team view (that role highlighted)
            "member": teams.member(s, *getattr(self, "_members", ({}, {})))}

    def markdown(self, arg: str = "") -> str:
        arg = arg.strip()
        number, _, text = arg.partition(" ")
        if number.isdigit() and text.strip():  # no sender here: the chat it came from is unknown
            n, text = int(number), text.strip()
            s = (shorts.ask_stop(self.state_dir, self.channels, n, where="chat") if text == "stop"
                 else shorts.reply(self.state_dir, self.channels, n, text, where="chat"))
            if s is None:
                return t("short.no_such", n=n)
            if s["status"] != "running":
                return t("short.ended", n=n)
            return t("short.stop_sent", n=n) if text == "stop" else t("short.sent", n=n)
        if arg.isdigit():
            s = shorts.load(self.state_dir, int(arg))
            return f"**{shorts.title(s)}**\n\n{shorts.markdown(s)}" if s else t("short.no_such", n=arg)
        return shorts.board(self.state_dir, everything=(arg == "all"))
