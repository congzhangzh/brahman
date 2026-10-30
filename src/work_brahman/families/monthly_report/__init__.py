"""Family: monthly report. One story per month: what our own work did, as a card for the
owner, with a Gantt chart drawn in text. See FAMILY.md. Read-only.

Data: GitHub (one GraphQL search through gh: every PR of mine updated in the month), the
GitLab projects listed under [duties.code-review] (anonymous REST), the upstream-mr
family for whose turn each open one is, and the buddy's own run records and asks.
"""

import calendar
import json
import math
import subprocess
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ...duties.code_review import forge
from ..upstream_mr import UpstreamMR
from ...text import month_abbr, month_day, month_year, t

WIDTH = 20  # characters for the month in the Gantt chart: fits a phone beside a 13-char label
LABEL = 14
STALE_DAYS = 30  # open this long with the turn on upstream: ask whether to ping, wait or drop


class MonthlyReport:
    name = "monthly-report"

    def __init__(self, state_dir: Path, cfg: dict | None = None):
        cr = (cfg or {}).get("duties", {}).get("code-review", {})
        self.state_dir = state_dir
        self.gitlab, self.gitlab_user = cr.get("gitlab", []), cr.get("gitlab_user", "")

    def commands(self) -> dict:
        return {"/report": (t("mr.help"), self.markdown)}

    def scheduled(self) -> str:
        """On the 1st: the month that just ended."""
        last = datetime.now().astimezone().replace(day=1) - timedelta(days=1)
        return self.markdown(f"{last:%Y-%m}")

    def markdown(self, arg: str = "") -> str:
        words = arg.split()
        full = "all" in words
        month = next((w for w in words if w != "all"), f"{datetime.now():%Y-%m}")
        try:
            start = datetime.strptime(month, "%Y-%m").astimezone()
        except ValueError:
            return t("mr.bad_month", month=repr(month))
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1]) + timedelta(days=1)
        now = min(datetime.now().astimezone(), end)
        prs, me, errors = self._prs(start)
        upstream = [p for p in prs if not p["repo"].startswith(f"{me}/")]
        own = len(prs) - len(upstream)
        # Inside the month: started before its end, and not ended before its start.
        live = [p for p in upstream if p["created"] < end and (p["ended"] is None or p["ended"] >= start)]
        opened = [p for p in live if p["created"] >= start]
        merged = [p for p in live if p["state"] == "MERGED" and p["ended"] and p["ended"] >= start]
        closed = [p for p in live if p["state"] == "CLOSED" and p["ended"] and p["ended"] >= start]
        still = [p for p in live if p["state"] == "OPEN"]
        turns = {s["url"]: s for s in UpstreamMR(self.state_dir).stories() if s["url"]}
        yours = [p for p in still if turns.get(p["url"], {}).get("turn") == "you"]
        stale = [p for p in still if turns.get(p["url"], {}).get("turn") == "upstream"
                 and (now - p["created"]).days >= STALE_DAYS]
        to_date = "" if now >= end else t("mr.to_date", day=month_day(now))

        out = [t("mr.head", month=month_year(start), to_date=to_date, merged=len(merged), opened=len(opened),
                 closed=len(closed), open=len(still), yours=len(yours))]
        if merged:
            by_repo: dict[str, int] = {}
            for p in merged:
                by_repo[p["repo"].rsplit("/", 1)[-1]] = by_repo.get(p["repo"].rsplit("/", 1)[-1], 0) + 1
            out += ["", t("mr.merged") + " · ".join(f"{r} {n}" for r, n in
                                                      sorted(by_repo.items(), key=lambda x: -x[1])),
                    "```", *_weeks(merged, start, end), "```"]
            if full:
                out += [f"- [{_label(p)}]({p['url']}) {p['title'][:60]}" for p in merged]
        shown = live if full else still
        if shown:
            what = t("mr.everything") if full else t("mr.still_open")
            out += ["", t("mr.timeline", what=what),
                    "```", *_gantt(shown, start, end, now), "```"]
        if yours:
            out += ["", t("mr.your_turn")] + [f"- [{_short(p)}]({p['url']}) {turns[p['url']]['why']}"
                                            for p in yours]
        if stale:
            out += ["", t("mr.stale", days=STALE_DAYS)]
            out += [f"- [{_short(p)}]({p['url']}) {t('mr.days', n=(now - p['created']).days)}" for p in stale]
        out += ["", self._buddy(start, end)]
        if own:
            out += [t("mr.own", n=own)]
        out += [f"⚠️ {e}" for e in errors]
        return "\n".join(out)

    def _prs(self, start: datetime) -> tuple[list[dict], str, list[str]]:
        """Every PR/MR of mine updated since start: (prs, my GitHub login, errors)."""
        prs, errors, me = [], [], ""
        query = ('query { viewer { login } search(query: "is:pr author:@me updated:>=%s", type: ISSUE,'
                 ' first: 100) { issueCount nodes { ... on PullRequest { number title url state'
                 ' createdAt mergedAt closedAt repository { nameWithOwner } } } } }' % f"{start:%Y-%m-%d}")
        p = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"],
                           capture_output=True, text=True, timeout=120)
        data = json.loads(p.stdout or "{}").get("data")
        if data:
            me = data["viewer"]["login"]
            s = data["search"]
            if s["issueCount"] > 100:
                errors.append(t("mr.gh_cap", n=s["issueCount"]))
            for n in s["nodes"]:
                prs.append(_pr(f"{n['repository']['nameWithOwner']}#{n['number']}", n["repository"]["nameWithOwner"],
                               n["title"], n["url"], n["state"], n["createdAt"], n["mergedAt"] or n["closedAt"]))
        else:
            errors.append(f"GitHub: {(p.stderr or p.stdout).strip()[:200]}")
        for project in self.gitlab:
            host, path = project.split("/", 1)
            api = (f"https://{host}/api/v4/projects/{urllib.parse.quote(path, safe='')}/merge_requests?"
                   + urllib.parse.urlencode({"author_username": self.gitlab_user, "state": "all",
                                             "updated_after": start.isoformat(), "per_page": 100}))
            got = forge._get_json(api)
            if not isinstance(got, list):
                errors.append(t("mr.gl_none", project=project))
                continue
            for m in got:
                state = forge.GL_STATE.get(m["state"], "OPEN")
                prs.append(_pr(f"{path}!{m['iid']}", project, m["title"], m["web_url"], state,
                               m["created_at"], m.get("merged_at") or m.get("closed_at")))
        return prs, me, errors

    def _buddy(self, start: datetime, end: datetime) -> str:
        """The buddy's own month: runs, failures, list-price dollars, asks."""
        runs = [r for d in sorted((self.state_dir / "runs").glob(f"{start:%Y-%m}-*"))
                for r in (_read(f) for f in d.glob("*.json")) if r.get("id")]
        failed = sum(r.get("status") in ("failed", "timeout", "interrupted") for r in runs)
        usd = sum((r.get("fitness") or {}).get("usd_list") or 0 for r in runs)
        asks = [a for a in (_read(f) for f in (self.state_dir / "asks").glob("*.json"))
                if a.get("asked", "")[:7] == f"{start:%Y-%m}"]
        answered = sum(bool(a.get("answer")) for a in asks)
        since = min((r["started"][:10] for r in runs if r.get("started")), default="")
        return (t("mr.buddy", runs=len(runs), failed=failed, usd=f"{usd:.2f}", answered=answered, asks=len(asks))
                + (t("mr.since", since=since) if since and since > f"{start:%Y-%m-%d}" else ""))


def _pr(key, repo, title, url, state, created, ended) -> dict:
    return {"key": key, "repo": repo, "title": title, "url": url, "state": state,
            "created": _when(created), "ended": _when(ended) if state != "OPEN" else None}


def _when(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def _label(p: dict) -> str:
    """FreeRDP/FreeRDP#13805 -> FreeRDP#13805; GNOME/gnome-remote-desktop!442 -> gnome-remote-desktop!442."""
    return p["key"].rsplit("/", 1)[-1]


def _short(p: dict) -> str:
    """A label that keeps its number within LABEL: gnome-remote-desktop!442 -> grd!442."""
    label = _label(p)
    if len(label) <= LABEL:
        return label
    name, sep, num = label.rpartition("#") if "#" in label else label.rpartition("!")
    if "-" in name:
        name = "".join(w[0] for w in name.split("-"))
    return (name[:LABEL - len(num) - 1] + sep + num)[:LABEL]


def _weeks(merged: list[dict], start: datetime, end: datetime) -> list[str]:
    """Merged per week of the month (days 1-7, 8-14, ...): a bar each."""
    days = (end - start).days
    counts = [0] * math.ceil(days / 7)
    for p in merged:
        counts[min((p["ended"] - start).days, days - 1) // 7] += 1
    top = max(counts) or 1
    return [f"{month_abbr(start)} {1 + 7 * i:>2}-{min(7 * i + 7, days):<2} {'█' * math.ceil(n / top * WIDTH)} {n}"
            for i, n in enumerate(counts)]


def _gantt(prs: list[dict], start: datetime, end: datetime, now: datetime) -> list[str]:
    """One text row per PR, sorted by start; the month is WIDTH characters wide."""
    span = (end - start).total_seconds()

    def col(t: datetime) -> float:
        return min(max((t - start).total_seconds() / span, 0), 1) * WIDTH

    rows = []
    for p in sorted(prs, key=lambda p: (p["created"], p["key"])):
        a = int(col(p["created"]))
        b = max(math.ceil(col(p["ended"] or now)), a + 1)
        mark = {"MERGED": "M", "CLOSED": "x"}.get(p["state"], ">")
        bar = "·" * a + "█" * (b - a - 1) + mark + "·" * (WIDTH - b)
        rows.append(f"{_short(p):<{LABEL}} {bar}")
    days = (end - start).days
    axis = [" "] * WIDTH
    for d in (1, 8, 15, 22, 29):
        if d <= days:
            c = int((d - 1) / days * WIDTH)
            for i, ch in enumerate(str(d)):
                if c + i < WIDTH:
                    axis[c + i] = ch
    return [f"{'':<{LABEL}} {''.join(axis)}"] + rows


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
