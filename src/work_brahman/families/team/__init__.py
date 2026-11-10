"""Family: team. One live card per kit, one row per long-lived member (RM, PO, area members),
linked to its short stories. Not a story: a team has no steps and no end. See FAMILY.md and
specs/team.md; the board itself is teams.py, written by `buddy team ...` (the RM runs it).
The workbench shows a tile per team, from its file only; its card (which looks at the live
sessions and windows, about a second) is drawn when the tile is picked (detail)."""

from pathlib import Path

from ... import short_stories as shorts, teams
from ...text import t


class Team:
    name = "team"

    def __init__(self, state_dir: Path, cfg: dict | None = None):
        self.state_dir = state_dir
        self.opts = teams.settings(cfg)
        self.channels = []  # set by `buddy serve`, so /team NAME <text> changes the cards

    def commands(self) -> dict:
        return {"/team": (t("team.help"), self.markdown)}

    def stories(self) -> list[dict]:
        return teams.all_teams(self.state_dir)

    def workflow(self) -> dict:
        """Its row in the workbench's level 1: a tile per team (a team never ends)."""
        return {"kind": "family", "name": self.name, "schedule": "", "about": t("team.about")}

    def runs(self, days: int | None = 3, full: int | None = None) -> list[dict]:
        """A tile per team, shaped like a run record: its joins, handovers and retirements are
        its steps (the last 30). Every team, whatever `days`: a team never ends."""
        return [self._run(tm) for tm in teams.all_teams(self.state_dir)]

    def detail(self, key: str) -> dict | None:
        """One team with its card as it is now, for the page when its tile is picked."""
        tm = teams.load(self.state_dir, key)
        if tm is None:
            return None
        run = self._run(tm)
        run["team"]["markdown"] = teams.show(self.state_dir, key, self.opts) or ""
        run["team"]["rows"] = self._rows(tm)
        return run

    def _rows(self, tm: dict) -> list[dict]:
        """A row per member, RM first, with every short story its sessions started (any
        generation): running first, then the newest. A story recorded for another team stays
        there."""
        stories = shorts.all_stories(self.state_dir)
        rows = []
        for x in sorted(tm["roster"], key=lambda x: teams.ORDER.get(x["kind"], 9)):
            r = teams.role(self.state_dir, x["role"]) or {"generations": []}
            names = {g["name"] for g in r.get("generations", [])}
            mine = [s for s in stories if s.get("who") in names and s.get("team", tm["team"]) == tm["team"]]
            mine.sort(key=lambda s: (s["status"] != "running", -s["id"]))
            rows.append({"kind": x["kind"], "role": x["role"], "area": r.get("area", ""),
                         "name": teams._who(self.state_dir, x["role"]),
                         "stories": [{"n": s["id"], "title": s["title"], "status": s["status"],
                                      "started": s["started"]} for s in mine]})
        return rows

    def _run(self, tm: dict) -> dict:
        rm = next((x["role"] for x in tm["roster"] if x["kind"] == "rm"), "")
        events = tm.get("events", [])
        return {
            "id": f"{self.name}:{tm['team']}", "kind": "family", "name": self.name,
            "trigger": t("team.board_line", team=tm["team"], rm=teams._who(self.state_dir, rm) if rm else "-",
                         n=len(tm["roster"])),
            "status": "ok", "started": tm["started"], "ended": None, "version": "",
            "steps": [{"step": e["text"], "status": "done", "at": e["at"]} for e in events[-30:]],
            "result": events[-1]["text"] if events else "",
            "fitness": {},
            "team": {"name": tm["team"], "rm": rm, "roles": len(tm["roster"])}}

    def markdown(self, arg: str = "") -> str:
        name, _, text = arg.strip().partition(" ")
        if not name:
            return teams.board(self.state_dir)
        if text.strip():  # no sender here: the chat it came from is unknown
            tm = teams.reply(self.state_dir, self.channels, name, text.strip(), where="chat", opts=self.opts)
            return t("team.sent", team=name) if tm else t("team.no_such", team=name)
        return teams.show(self.state_dir, name, self.opts) or t("team.no_such", team=name)
