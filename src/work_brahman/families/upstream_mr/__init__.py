"""Family: upstream MR. Every story (one change for one upstream project) with its stage and
whose turn it is. See FAMILY.md. Read-only.

Joins what already exists: the code-review duty's snapshot (submitted PRs/MRs, stage 6),
the threads duty's snapshot (who spoke last on GitHub) and the upstream ledger (changes
waiting for the owner's own hand, stage 5).
"""

import json
import os
import re
from pathlib import Path

from ...text import join, t

LEDGER = Path(os.environ.get("UPSTREAM_LEDGER", Path.home() / ".claude/upstream-ledger.md"))
ORDER = {"you": 0, "executor": 1, "upstream": 2}


class UpstreamMR:
    name = "upstream-mr"

    def __init__(self, state_dir: Path, cfg: dict | None = None, ledger: Path = LEDGER):
        self.state_dir, self.ledger = state_dir, ledger

    def commands(self) -> dict:
        return {"/stories": (t("um.help"), self.markdown)}

    def scheduled(self) -> str:
        return self.markdown()

    def stories(self) -> list[dict]:
        return sorted(self._ledger() + self._snapshot(), key=lambda s: (ORDER[s["turn"]], s["story"]))

    def markdown(self, arg: str = "") -> str:
        """For a phone: your turn and the executor's in a table; upstream's on one line,
        unless `all`."""
        stories = self.stories()
        if not stories:
            return t("um.none")
        count = {k: sum(s["turn"] == k for s in stories) for k in ORDER}
        out = [t("um.head", n=len(stories), **count), "", t("um.table"), "|---|---|---|"]
        full = arg.strip() == "all"
        for s in stories:
            if full or s["turn"] != "upstream":
                story = f"[{s['story']}]({s['url']})" if s["url"] else s["story"]
                out.append(f"| {t('um.turn.' + s['turn'])} | {story} | {s['why']} |")
        waiting = [s["story"] for s in stories if s["turn"] == "upstream"]
        if waiting and not full:
            out += ["", t("um.waiting", list=join(waiting))]
        return "\n".join(out)

    def _snapshot(self) -> list[dict]:
        prs, threads = _load(self.state_dir / "code-review.json", "prs"), \
            _load(self.state_dir / "threads.json", "items")
        out = []
        for key, p in prs.items():
            who, why = turn(p, threads.get(p.get("url", "")) if key.startswith("github:") else None)
            out.append({"story": _short(key), "title": p.get("title", ""), "stage": "6 review",
                        "turn": who, "why": why, "url": p.get("url", "")})
        return out

    def _ledger(self) -> list[dict]:
        """Ledger entries: '### <date> · <project> · <title> · <status>'."""
        try:
            text = self.ledger.read_text()
        except OSError:
            return []
        out = []
        for m in re.finditer(r"^### (\S+) · (\S+) · (.+?) · (.+)$", text, re.M):
            date, project, title, status = m.groups()
            if status.startswith("open"):
                out.append({"story": project, "title": title, "stage": "5 submit (by hand)",
                            "turn": "you", "why": t("um.why.ledger", date=date, title=title[:40]), "url": ""})
        return out


def turn(p: dict, thread: dict | None) -> tuple[str, str]:
    """(whose turn, why) for one PR/MR in the snapshot, by FAMILY.md's table.

    Comments: the threads duty knows who spoke last on GitHub (bots skipped); GitLab has no
    such record, so there a comment by someone else only says "unknown".
    """
    if p.get("review") == "changes":
        return "you", t("um.why.changes")
    if thread and thread.get("turn") == "you":
        return "you", t("um.why.spoke", last=thread.get("last"), at=thread.get("at", "")[:10])
    if not thread and p.get("talk"):
        last = p["talk"][-1]
        return "you", t("um.why.commented", by=last["by"], at=last["at"][:10])
    if p.get("merge") in ("conflict", "rebase"):
        return "you", t(f"um.why.{p['merge']}")
    if p.get("ci") == "fail":
        return "executor", t("um.why.ci")
    if p.get("review") == "approved":
        return "upstream", t("um.why.approved")
    return "upstream", t("um.why.review", ci=p.get("ci", "?"))


def _load(path: Path, field: str) -> dict:
    try:
        return json.loads(path.read_text())[field]
    except (OSError, ValueError, KeyError):
        return {}


def _short(key: str) -> str:
    """github:FreeRDP/FreeRDP#13805 -> FreeRDP#13805; gitlab:host/GNOME/x!442 -> x!442."""
    return re.sub(r"^.*/", "", key.split(":", 1)[1])
