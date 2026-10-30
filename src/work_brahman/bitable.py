"""A Lark/Feishu Base (Bitable) as a workbench: the buddy's runs, asks, PRs, threads,
workflows and short stories as tables, for Lark's own charts and dashboards.

One way only, buddy -> Base. Every minute (and on `buddy bitable`) the rows are rebuilt
from the files the buddy already writes (run records, asks, code-review.json,
threads.json), and only rows that changed are sent: new ones created, changed ones
updated, by a key per row. What was sent is remembered in state/bitable.json:

    {"app": token, "url": ..., "tables": {name: table id}, "rows": {name: {key: [record id, hash]}}}

On the first sync the buddy creates the Base in its own space and makes the person in
[bitable] share_with a full-access collaborator. Needs the app scopes bitable:app and
docs:permission.member:create. Rows never leave the tenant; the Base is as private as
any document the bot owns.
"""

import hashlib
import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import lark_oapi as lark

from . import short_stories as shorts
from .runs import read as read_run

log = logging.getLogger("buddy.bitable")

STAMP = "%Y-%m-%dT%H:%M:%S%z"
TEXT, NUMBER, SELECT, DATETIME, URL = 1, 2, 3, 5, 15
# table -> [(field, type)], the first field is the row's key (the primary field)
TABLES = {
    "Runs": [("Run", TEXT), ("Workflow", TEXT), ("Kind", SELECT), ("Status", SELECT),
             ("Started", DATETIME), ("Seconds", NUMBER), ("USD (list)", NUMBER), ("Tokens", NUMBER),
             ("Denied", NUMBER), ("Trigger", TEXT), ("Result", TEXT), ("Version", TEXT)],
    "Asks": [("Ask", TEXT), ("Title", TEXT), ("Question", TEXT), ("Options", TEXT),
             ("Answer", TEXT), ("Where", SELECT), ("Asked", DATETIME), ("Answered", DATETIME),
             ("Minutes to answer", NUMBER)],
    "PRs": [("PR", TEXT), ("Link", URL), ("Repo", TEXT), ("Title", TEXT), ("CI", SELECT),
            ("Review", SELECT), ("Merge", SELECT), ("Last comment", DATETIME)],
    "Threads": [("Thread", TEXT), ("Link", URL), ("Kind", SELECT), ("Repo", TEXT),
                ("Title", TEXT), ("Turn", SELECT), ("Last by", TEXT), ("At", DATETIME),
                ("Mine", NUMBER)],
    "Workflows": [("Workflow", TEXT), ("Schedule", TEXT), ("Runs (3 days)", NUMBER),
                  ("Failed (3 days)", NUMBER), ("USD (3 days)", NUMBER),
                  ("Tokens (3 days)", NUMBER), ("Last run", DATETIME), ("Last status", SELECT)],
    # Short stories (short_stories.py): sessions' pipeline-bound work, one row per story
    "Short stories": [("Story", TEXT), ("Title", TEXT), ("Status", SELECT), ("Who", TEXT),
                      ("Started", DATETIME), ("Ended", DATETIME), ("Minutes", NUMBER),
                      ("Marks done", NUMBER), ("Marks", NUMBER), ("Not planned", NUMBER),
                      ("Replies", NUMBER), ("Stop from", SELECT), ("Last event", TEXT)],
}
FAILED = ("failed", "timeout", "interrupted")


def _ms(stamp: str | None) -> int | None:
    if not stamp:
        return None
    for fmt in (STAMP, "%Y-%m-%dT%H:%M:%SZ"):  # ours carry an offset; GitHub's end in Z (UTC)
        try:
            d = datetime.strptime(stamp, fmt)
            return int((d if d.tzinfo else d.replace(tzinfo=UTC)).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def rows(state: Path, cfg: dict, days: int = 30) -> dict[str, dict[str, dict]]:
    """Every table's rows, {table: {key: fields}}, from the buddy's own files."""
    out = {name: {} for name in TABLES}
    runs = []
    for d in sorted((state / "runs").glob("*"))[-days:]:
        runs += [read_run(r) for r in (_read(f) for f in d.glob("*.json")) if r.get("id")]
    for r in runs:
        f = r.get("fitness") or {}
        out["Runs"][r["id"]] = {
            "Workflow": f"{r['kind']}:{r['name']}", "Kind": r["kind"], "Status": r["status"],
            "Started": _ms(r.get("started")), "Seconds": f.get("seconds"),
            "USD (list)": round(f.get("usd_list") or 0, 4),
            "Tokens": sum(f.get(k) or 0 for k in ("tokens_in", "tokens_cached", "tokens_out")),
            "Denied": f.get("denied") or 0, "Trigger": r.get("trigger", ""),
            "Result": str(r.get("result") or r.get("error") or "")[:500], "Version": r.get("version", "")}
    for f in (state / "asks").glob("*.json"):
        a = _read(f)
        if not a.get("id"):
            continue
        ans = a.get("answer") or {}
        asked, answered = _ms(a.get("asked")), _ms(ans.get("at"))
        out["Asks"][a["id"]] = {
            "Title": a.get("title", ""), "Question": a.get("markdown", "")[:1000],
            "Options": " / ".join(label for _, label in a.get("options", [])),
            "Answer": ans.get("label", ""), "Where": ans.get("where", "") or "open",
            "Asked": asked, "Answered": answered,
            "Minutes to answer": round((answered - asked) / 60000, 1) if asked and answered else None}
    for pr in _read(state / "code-review.json").get("prs", {}).values():
        talk = pr.get("talk") or []
        out["PRs"][pr["key"]] = {
            "Link": {"text": pr["url"], "link": pr["url"]}, "Repo": pr["repo"], "Title": pr["title"],
            "CI": pr["ci"], "Review": pr["review"], "Merge": pr["merge"],
            "Last comment": _ms(talk[-1]["at"]) if talk else None}
    for url, t in _read(state / "threads.json").get("items", {}).items():
        out["Threads"][f"{t['repo']}#{t['number']}"] = {
            "Link": {"text": url, "link": url}, "Kind": t["kind"], "Repo": t["repo"],
            "Title": t["title"], "Turn": t["turn"], "Last by": t.get("last") or "",
            "At": _ms(t.get("at")), "Mine": t.get("mine") or 0}
    since = time.time() - 3 * 86400
    for kind, section in (("job", "jobs"), ("duty", "duties")):
        for name, t in cfg.get(section, {}).items():
            rs = [r for r in runs if r["kind"] == kind and r["name"] == name]
            recent = [r for r in rs if (_ms(r.get("started")) or 0) / 1000 >= since]
            last = max(rs, key=lambda r: r.get("started", ""), default=None)
            fit = [r.get("fitness") or {} for r in recent]
            out["Workflows"][f"{kind}:{name}"] = {
                "Schedule": t.get("schedule", "not scheduled"), "Runs (3 days)": len(recent),
                "Failed (3 days)": sum(r["status"] in FAILED for r in recent),
                "USD (3 days)": round(sum(f.get("usd_list") or 0 for f in fit), 4),
                "Tokens (3 days)": sum(f.get(k) or 0 for f in fit for k in ("tokens_in", "tokens_cached", "tokens_out")),
                "Last run": _ms(last.get("started")) if last else None,
                "Last status": last["status"] if last else "never"}
    # A family is a workflow too, and its stories are its runs (the web's level 1 does the same).
    now = int(time.time() * 1000)
    for s in shorts.all_stories(state):
        status = "stopped" if s["status"] == "failed" and s.get("stop") else s["status"]
        started, ended = _ms(s["started"]), _ms(s["ended"])
        out["Short stories"][f"#{s['id']}"] = {
            "Title": s["title"], "Status": status, "Who": s.get("who") or "", "Started": started,
            "Ended": ended, "Minutes": round(((ended or now) - started) / 60000, 1) if started else None,
            "Marks done": sum(m["status"] in ("done", "skipped") for m in s["marks"]),
            "Marks": len(s["marks"]), "Not planned": sum(not m.get("planned", True) for m in s["marks"]),
            "Replies": len(s.get("replies", [])), "Stop from": (s.get("stop") or {}).get("where"),
            "Last event": s["events"][-1]["text"][:500] if s["events"] else ""}
    recent = [r for r in out["Short stories"].values() if (r.get("Started") or 0) / 1000 >= since]
    last = max(out["Short stories"].values(), key=lambda r: r.get("Started") or 0, default=None)
    out["Workflows"]["family:short-story"] = {
        "Schedule": "started by sessions", "Runs (3 days)": len(recent),
        "Failed (3 days)": sum(r["Status"] in ("failed", "stopped") for r in recent),
        "USD (3 days)": 0, "Tokens (3 days)": 0,  # the buddy only keeps the board: no agent of its own
        "Last run": last.get("Started") if last else None,
        "Last status": last["Status"] if last else "never"}
    # Lark rejects null values; an empty cell is simply left out
    return {t: {k: {f: v for f, v in fields.items() if v is not None} for k, fields in rs.items()}
            for t, rs in out.items()}


class Bitable:
    def __init__(self, app_id: str, app_secret: str, domain, state: Path, cfg: dict):
        self.api = (lark.Client.builder().app_id(app_id).app_secret(app_secret).domain(domain)
                    .log_level(lark.LogLevel.WARNING).build())
        self.state, self.cfg = state, cfg
        self.share_with = cfg.get("bitable", {}).get("share_with", "")
        self.path = state / "bitable.json"
        self.lock = threading.Lock()

    def start(self, every: int = 60) -> None:
        def loop():
            while True:
                try:
                    n = self.sync()
                    if n:
                        log.info("bitable: %d rows sent", n)
                except Exception:
                    log.exception("bitable sync failed")
                time.sleep(every)
        threading.Thread(target=loop, name="bitable", daemon=True).start()

    def sync(self) -> int:
        """Create the Base on the first call, then send what changed. Returns rows sent."""
        with self.lock:
            st = _read(self.path) or {"rows": {}}
            if not st.get("app"):
                self._create(st)
                self._save(st)
            for name in TABLES.keys() - st["tables"].keys():  # added since the Base was made
                self._table(st, name)
                self._save(st)
            sent = 0
            for table, want in rows(self.state, self.cfg).items():
                have = st["rows"].setdefault(table, {})
                new, changed = [], []
                for key, fields in want.items():
                    fields = {TABLES[table][0][0]: key, **fields}
                    h = hashlib.sha1(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:12]
                    if key not in have:
                        new.append((key, fields, h))
                    elif have[key][1] != h:
                        changed.append((key, fields, h))
                tid = st["tables"][table]
                for i in range(0, len(new), 500):
                    part = new[i:i + 500]
                    made = self._call("POST", f"/apps/{st['app']}/tables/{tid}/records/batch_create",
                                      {"records": [{"fields": f} for _, f, _ in part]})["records"]
                    for (key, _, h), rec in zip(part, made):
                        have[key] = [rec["record_id"], h]
                for i in range(0, len(changed), 500):
                    part = changed[i:i + 500]
                    self._call("POST", f"/apps/{st['app']}/tables/{tid}/records/batch_update",
                               {"records": [{"record_id": have[k][0], "fields": f} for k, f, _ in part]})
                    for key, _, h in part:
                        have[key][1] = h
                sent += len(new) + len(changed)
                self._save(st)
            return sent

    def url(self) -> str:
        return _read(self.path).get("url", "")

    def _create(self, st: dict) -> None:
        app = self._call("POST", "/apps", {"name": "brahman workbench"})["app"]
        st.update(app=app["app_token"], url=app.get("url", ""), tables={}, rows={})
        for name in TABLES:
            self._table(st, name)
        if app.get("default_table_id"):  # the empty table every new Base starts with
            self._call("DELETE", f"/apps/{app['app_token']}/tables/{app['default_table_id']}", None)
        if self.share_with:
            self._call("POST", f"/permissions/{app['app_token']}/members?type=bitable&need_notification=true",
                       {"member_type": "openid", "member_id": self.share_with, "perm": "full_access"},
                       base="/open-apis/drive/v1")
        log.info("bitable: created %s", st["url"])

    def _table(self, st: dict, name: str) -> None:
        t = self._call("POST", f"/apps/{st['app']}/tables", {"table": {
            "name": name, "default_view_name": "All",
            "fields": [{"field_name": f, "type": kind} for f, kind in TABLES[name]]}})
        st["tables"][name] = t["table_id"]
        log.info("bitable: table %s added", name)

    def _call(self, method: str, path: str, body, base: str = "/open-apis/bitable/v1") -> dict:
        req = (lark.BaseRequest.builder().http_method(getattr(lark.HttpMethod, method))
               .uri(base + path).token_types({lark.AccessTokenType.TENANT}))
        if body is not None:
            req = req.body(body)
        resp = self.api.request(req.build())
        d = json.loads(resp.raw.content or b"{}")
        if d.get("code"):
            raise RuntimeError(f"lark {method} {path.split('?')[0]}: {d.get('code')} {d.get('msg')}")
        return d.get("data") or {}

    def _save(self, st: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False))
        tmp.replace(self.path)
