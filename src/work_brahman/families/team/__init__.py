"""Family: team. One live card per kit, one row per long-lived member (RM, PO, area members),
linked to its short stories. Not a story: a team has no steps and no end. See FAMILY.md and
specs/team.md; the board itself is teams.py, written by `buddy team ...` (the RM runs it)."""

from pathlib import Path

from ... import teams
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
        """Its row in the workbench's level 1. It has no runs: a team never ends."""
        return {"kind": "family", "name": self.name, "schedule": "", "about": t("team.about")}

    def runs(self, days: int = 3) -> list[dict]:
        return []

    def markdown(self, arg: str = "") -> str:
        name, _, text = arg.strip().partition(" ")
        if not name:
            return teams.board(self.state_dir)
        if text.strip():  # no sender here: the chat it came from is unknown
            tm = teams.reply(self.state_dir, self.channels, name, text.strip(), where="chat", opts=self.opts)
            return t("team.sent", team=name) if tm else t("team.no_such", team=name)
        return teams.show(self.state_dir, name, self.opts) or t("team.no_such", team=name)
