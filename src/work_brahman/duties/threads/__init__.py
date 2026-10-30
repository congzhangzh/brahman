"""Duty: threads. Say when a GitHub conversation I take part in turns to me. See DUTY.md."""

import json
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ... import portable
from ..code_review import forge
from ...text import t

ORDER = {"you": 0, "new": 1, "them": 2}  # the three turns, in table order
KIND = ("PR", "issue", "discussion")  # shown as text.py's th.kind.*


class Threads:
    name = "threads"

    @property
    def note(self) -> str:
        return t("th.note")

    def __init__(self, cfg: dict, state_dir: Path):
        self.chat_id = cfg.get("chat_id", "")
        self.cmd = list(cfg.get("command", ["threads", "--json"]))  # the L1 tool
        self.digest = cfg.get("digest", "")  # "09:00": once a day, the whole list
        self.latest_days = cfg.get("latest_days", 3)  # the list's top part: active this recently
        self.state_dir = state_dir
        self.path = state_dir / "threads.json"

    def commands(self) -> dict:
        return {"/threads": (t("th.help"), lambda arg: self.update())}

    def tick(self, now: float | None = None) -> tuple[str, str]:
        """Fetch, compare, save. Returns (the notice: changes first, then the whole list with
        changed rows marked *; empty when nothing turned to me), the whole list."""
        now = time.time() if now is None else now
        with self._state() as st:
            first = "checked" not in st
            items, error = self._fetch()
            if items is None:  # the tool failed or is still filling its cache: keep the snapshot
                items = st.get("items", {})
            old = st.get("items", {}) if not first else items
            changed = {u for u, t in items.items() if old.get(u) != t}
            mine = [u for u in changed if _turned_to_me(old.get(u), items[u])
                    and u not in self._watched_elsewhere()]
            st["items"], st["checked"] = items, time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
            table = _table(items, changed, now, self.latest_days)
            digest = self._digest_due(st, now)
            if first:
                n = sum(x["turn"] == "you" for x in items.values())
                notice = t("th.first", n=len(items), k=n) + table
            elif mine or digest:
                head = (_changes(items, mine) if mine
                        else t("th.digest", k=sum(x["turn"] == "you" for x in items.values())))
                notice = head + "\n\n" + table
            else:
                notice = ""
            if error:
                notice = (notice + "\n\n" if notice else "") + f"⚠️ {error}"
            return notice, table

    def update(self) -> str:
        notice, table = self.tick()
        return notice or t("th.nothing") + table

    def _fetch(self) -> tuple[dict | None, str]:
        """{url: thread} from the L1 tool, or (None, why). The only step that does I/O."""
        try:
            p = subprocess.run(self.cmd, capture_output=True, text=True, timeout=180)
            data = json.loads(p.stdout or "{}")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            return None, f"threads: {e}"
        if data.get("refreshing"):
            return None, ""  # a cold cache: the next check has data
        if p.returncode or "items" not in data:
            return None, f"threads failed ({p.returncode}): {p.stderr.strip()[-200:]}"
        keep = ("kind", "repo", "number", "title", "url", "last", "at", "turn", "mine")
        return {t["url"]: {k: t.get(k) for k in keep} for t in data["items"]}, ""

    def _watched_elsewhere(self) -> set[str]:
        """URLs of PRs the code-review duty already reports comments on."""
        try:
            st = json.loads((self.state_dir / "code-review.json").read_text())
        except (OSError, json.JSONDecodeError):
            return set()
        return {pr["url"] for pr in st.get("prs", {}).values()}

    def _digest_due(self, st: dict, now: float) -> bool:
        today, hm = time.strftime("%Y-%m-%d", time.localtime(now)), time.strftime("%H:%M", time.localtime(now))
        if not self.digest or hm < self.digest or st.get("digest_day") == today:
            return False
        st["digest_day"] = today
        return True

    @contextmanager
    def _state(self):
        """The state file, locked: a scheduled check and a chat /threads never interleave."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portable.locked(self.path.with_suffix(".lock")):
            st = json.loads(self.path.read_text()) if self.path.exists() else {}
            yield st
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False))
            os.replace(tmp, self.path)


def _turned_to_me(old: dict | None, new: dict) -> bool:
    """My turn now, and either it wasn't before or someone spoke again since."""
    return new["turn"] == "you" and (old is None or old["turn"] != "you" or old["at"] != new["at"])


def _short(t: dict) -> str:
    return f"{t['repo'].rsplit('/', 1)[-1]}#{t['number']}"


def _changes(items: dict, urls: list[str]) -> str:
    rows = sorted((items[u] for u in urls), key=lambda x: x["at"] or "")
    return "\n".join(t("th.your_turn", short=_short(x), url=x["url"], title=x["title"],
                       last=x["last"] or t("th.nobody"), at=(x["at"] or "")[:10]) for x in rows)


def _table(items: dict, changed: set[str], now: float | None = None, days: int = 3) -> str:
    """Two parts: what moved in the last `days`, newest first; then the rest, the threads
    I talk in most first (💬 = how often I spoke there, as the threads tool counts it)."""
    if not items:
        return t("th.none")
    since = (datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
             - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    latest = sorted((x for x in items.values() if (x["at"] or "") >= since),
                    key=lambda x: x["at"] or "", reverse=True)
    rest = sorted((x for x in items.values() if (x["at"] or "") < since),
                  key=lambda x: (-(x.get("mine") or 0), _rank(x), x["at"] or ""))
    lines = []
    for label, rows in ((t("th.latest", days=days), latest), (t("th.most"), rest)):
        if not rows:
            continue
        lines += ["" if lines else None, label, "", t("th.head"), "|---|---|---|---|---|"]
        for x in rows:
            mark = "* " if x["url"] in changed else ""
            title = x["title"] if len(x["title"]) <= 36 else x["title"][:35] + "…"
            turn = t(f"th.turn.{x['turn']}") if x["turn"] in ORDER else x["turn"]
            kind = t(f"th.kind.{x['kind']}") if x["kind"] in KIND else x["kind"]
            lines.append(f"| {mark}{turn} | [{kind} {_short(x)}]({x['url']}) "
                         f"| {x.get('mine') or 0} | {(x['last'] or '-')[:12]} {(x['at'] or '')[5:10]} | {title} |")
    n = {k: sum(x["turn"] == k for x in items.values()) for k in ORDER}
    return ("\n".join(x for x in lines if x is not None)
            + t("th.foot", **n) + (t("th.changed") if changed else ""))


def _rank(t: dict) -> int:
    return ORDER.get(t["turn"], 3)  # equal talk: my turn before theirs
