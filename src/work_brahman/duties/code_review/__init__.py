"""Duty: code review. Watch my pull/merge requests and say what changed. See DUTY.md."""

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from ... import portable
from . import forge, rebase
from ...text import join, t, tn

CI = {"pass": "✓", "fail": "✗", "running": "…", "none": "-"}
REVIEW = {"approved": "👍", "changes": "✋", "-": ""}
MERGE = {"conflict": "cr.merge.conflict", "rebase": "cr.merge.rebase"}  # text keys; ok and - show nothing


class CodeReview:
    name = "code-review"

    @property
    def note(self) -> str:  # under each notice
        return t("cr.note")

    def __init__(self, cfg: dict, state_dir: Path):
        self.chat_id = cfg.get("chat_id", "")
        self.gitlab = list(cfg.get("gitlab", []))        # "host/group/project"
        self.gitlab_user = cfg.get("gitlab_user", "")
        self.ignore = set(cfg.get("ignore_authors", []))  # CI bots that post as users
        self.path = state_dir / "code-review.json"
        self.drafts = state_dir / "rebase"                # scratch worktrees, one per PR
        self.clone_roots = list(cfg.get("clone_roots", ["~/projects"]))  # where my clones are
        self.push_repos = set(cfg.get("push_repos", []))  # forks the buddy may push to, on a yes
        self.git_url = cfg.get("git_url", "")             # tests only: a URL template
        self.offers: list[tuple[str, str, list, dict]] = []  # asks to send after this check

    # Chat commands: name -> (help, fn(argument) -> Markdown reply).
    def commands(self) -> dict:
        return {
            "/update": (t("cr.help.update"), lambda arg: self.update()),
            "/watch": (t("cr.help.watch"), self.watch),
            "/unwatch": (t("cr.help.unwatch"), self.unwatch),
        }

    def tick(self) -> tuple[str, str]:
        """Fetch, compare with the last snapshot, save. Returns (what changed, the table)."""
        with self._state() as st:
            first = "checked" not in st  # the first run only learns, it reports nothing
            now, errors = self._fetch(st)
            for key, pr in now.items():  # "?": the forge hasn't decided yet; keep what it said last
                if pr["merge"] == "?":
                    pr["merge"] = st["prs"].get(key, {}).get("merge", "ok")
            events = [] if first else _diff(st["prs"], now)
            for key, pr in now.items():  # finished ones are reported once, then dropped
                if pr["state"] != "OPEN":
                    st["manual"] = [k for k in st["manual"] if k != key]
            st["prs"] = {k: v for k, v in now.items() if v["state"] == "OPEN"}
            st["checked"] = time.strftime("%Y-%m-%d %H:%M")
            changes = _render_events(events, now)
            merge_lines = (t("cr.ev.conflict"), t("cr.ev.rebase"))
            for key, line in events:  # a conflict or a needed rebase: offer to draft one
                pr = now[key]
                if line in merge_lines and pr.get("base") and pr["state"] == "OPEN":
                    self.offers.append((t("cr.offer.rebase", short=_short(pr), url=pr["url"],
                                          what=line.split(" ", 1)[1]),
                                        t("cr.title.rebase", short=_short(pr)),
                                        [["draft", t("cr.opt.draft")], ["leave", t("btn.leave")]],
                                        {"draft": ["duty", self.name, f"draft {key}"]}))
            if first:
                changes = t("cr.first", n=len(st["prs"]))
            # A forge error is news once: when it starts (or changes), and when it clears.
            # While it lasts, every check would otherwise post the same warning.
            said = st.get("errors", [])
            if errors != said:
                news = [f"⚠️ {e}" for e in errors if e not in said]
                news += [t("cr.fixed", what=e.split(":", 1)[0]) for e in said if e not in errors]
                if news:
                    changes += ("\n\n" if changes else "") + "\n".join(news)
                st["errors"] = errors
            return changes, _table(st["prs"])

    def act(self, arg: str) -> str:
        """What an answer asked for: "draft KEY [choice]", "push KEY" or "drop KEY".
        Returns the Markdown to post; follow-up questions go to self.offers."""
        verb, _, rest = arg.partition(" ")
        key, _, choice = rest.partition(" ")
        with self._state() as st:
            pr = st["prs"].get(key)
            drafts = st.setdefault("drafts", {})
            if pr is None:
                return t("cr.not_watched", key=key)
            short, where = f"[{_short(pr)}]({pr['url']})", self.drafts / re.sub(r"\W+", "-", key)
            if verb == "draft":
                clone = rebase.find_clone(pr, self.clone_roots)
                if clone is None:
                    return t("cr.no_clone", short=short, repo=pr["repo"], roots=", ".join(self.clone_roots))
                d = rebase.draft(pr, clone, where, choice, self.git_url)
                drafts[key] = d
                return self._after_draft(pr, short, d, key)
            d = drafts.pop(key, None)
            if d is None:
                return t("cr.no_draft", short=short, verb=t(f"cr.verb.{verb}") if verb in ("push", "drop") else verb)
            if verb == "push":
                url = rebase.push(pr, d, self.git_url)
                return t("cr.pushed", short=short, new=d["new"][:12], url=url)
            rebase.remove(Path(d["clone"]), Path(d["wt"]))
            return t("cr.dropped", short=short)

    def _after_draft(self, pr: dict, short: str, d: dict, key: str) -> str:
        if d["status"] == "current":
            return t("cr.current", short=short, base=pr["base"])
        if d["status"] == "left":
            return t("cr.left", short=short, wt=d["wt"], files=", ".join(d["files"]))
        if d["status"] == "conflict":
            self.offers.append((
                t("cr.conflict", short=short, base=pr["base"], files=join(f"`{f}`" for f in d["files"][:8])),
                t("cr.title.conflict", short=_short(pr)),
                [["mine", t("cr.opt.mine")], ["upstream", t("cr.opt.upstream")],
                 ["myself", t("cr.opt.myself")]],
                {c: ["duty", self.name, f"draft {key} {c}"] for c in ("mine", "upstream", "myself")}))
            return ""
        guessed = t("cr.guessed") if pr.get("head_repo_guessed") else ""
        summary = (t("cr.rebased", short=short, base=pr["base"], wt=d["wt"], n=len(d["commits"]), stat=d["stat"])
                   + "\n".join(f"- `{c}`" for c in d["commits"][:10]))
        if pr["head_repo"] in self.push_repos:
            self.offers.append((summary + t("cr.push_offer", repo=pr["head_repo"], guessed=guessed),
                                t("cr.title.push", short=_short(pr)),
                                [["accept", t("cr.opt.push")], ["reject", t("cr.opt.drop")]],
                                {"accept": ["duty", self.name, f"push {key}"],
                                 "reject": ["duty", self.name, f"drop {key}"]}))
            return ""
        self.offers.append((summary + t("cr.push_yourself", repo=pr["head_repo"], guessed=guessed,
                                        cmd=rebase.push_command(pr, d, self.git_url)),
                            t("cr.title.ready", short=_short(pr)), [["reject", t("cr.opt.drop")]],
                            {"reject": ["duty", self.name, f"drop {key}"]}))
        return ""

    def update(self) -> str:
        changes, table = self.tick()
        return (changes + "\n\n" if changes else t("cr.no_changes")) + table

    def watch(self, arg: str) -> str:
        key = forge.parse_url(arg.strip())
        if not key:
            return t("cr.give_link")
        with self._state() as st:
            st["muted"] = [k for k in st["muted"] if k != key]
            if key not in st["manual"]:
                st["manual"].append(key)
        return t("cr.watching", key=key)

    def unwatch(self, arg: str) -> str:
        key = forge.parse_url(arg.strip()) or arg.strip()
        with self._state() as st:
            st["manual"] = [k for k in st["manual"] if k != key]
            st["prs"].pop(key, None)
            if key not in st["muted"]:
                st["muted"].append(key)  # else my own open PR comes back on the next check
        return t("cr.unwatched", key=key)

    def _fetch(self, st: dict) -> tuple[dict, list[str]]:
        """Current state of everything to watch. A forge that fails keeps its last snapshot,
        so an outage never reads as "merged" or "closed"."""
        old, muted = st["prs"], set(st["muted"])
        tracked = set(old) | set(st["manual"])
        now, errors = {}, []
        try:
            now.update(forge.github(sorted(k for k in tracked if k.startswith("github:")), self.ignore))
        except Exception as e:
            errors.append(str(e))
            now.update({k: v for k, v in old.items() if k.startswith("github:")})
        projects = set(self.gitlab) | {forge.split_key(k)[1] for k in tracked if k.startswith("gitlab:")}
        for proj in sorted(projects):
            extra = [forge.split_key(k)[2] for k in tracked if k.startswith(f"gitlab:{proj}!")]
            try:
                now.update(forge.gitlab(proj, self.gitlab_user, extra, self.ignore))
            except Exception as e:
                errors.append(f"gitlab {proj}: {e}")
                now.update({k: v for k, v in old.items() if k.startswith(f"gitlab:{proj}!")})
        for k in tracked - set(now):  # not returned at all (repo gone?): keep it, quietly
            if k in old:
                now[k] = old[k]
        return {k: v for k, v in now.items() if k not in muted}, errors

    @contextmanager
    def _state(self):
        """The state file, locked: a timer tick and a chat /update never interleave."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portable.locked(self.path.with_suffix(".lock")):
            st = json.loads(self.path.read_text()) if self.path.exists() else {}
            for field in ("manual", "muted"):
                st.setdefault(field, [])
            st.setdefault("prs", {})
            yield st
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False))
            os.replace(tmp, self.path)


def _diff(old: dict, new: dict) -> list[tuple[str, str]]:
    """(key, line) for each change worth a message."""
    out = []
    for key, n in new.items():
        o = old.get(key)
        if o is None:
            if n["state"] == "OPEN":
                out.append((key, t("cr.ev.new")))
            continue
        if n["state"] != o["state"]:
            out.append((key, t({"MERGED": "cr.ev.merged", "CLOSED": "cr.ev.closed"}.get(n["state"], "cr.ev.reopened"))))
            continue
        if n["ci"] != o["ci"] and (n["ci"] == "fail" or o["ci"] == "fail"):
            out.append((key, t("cr.ev.ci_failed" if n["ci"] == "fail" else "cr.ev.ci_passed")))
        if n["review"] != o["review"] and n["review"] != "-":
            out.append((key, t("cr.ev.approved" if n["review"] == "approved" else "cr.ev.changes")))
        if n["merge"] != o["merge"] and n["merge"] in ("conflict", "rebase"):
            out.append((key, t("cr.ev.conflict" if n["merge"] == "conflict" else "cr.ev.rebase")))
        seen = o["talk"][-1]["at"] if o["talk"] else ""
        fresh = [t for t in n["talk"] if t["at"] > seen]
        shown = fresh[-COMMENTS_SHOWN:]
        if len(fresh) > len(shown):
            out.append((key, tn("cr.ev.earlier", len(fresh) - len(shown))))
        for c in shown:  # what each one says, not only that someone spoke
            on = t("cr.ev.on", path=c["path"]) if c.get("path") else ""
            out.append((key, t("cr.ev.comment", by=c["by"], on=on, url=c["url"],
                               text=c.get("body") or c["text"] or t("cr.no_text"))))
    return out


COMMENTS_SHOWN = 3  # the newest new comments on a card; older ones are counted


def _short(pr: dict) -> str:
    sep = "!" if pr["key"].startswith("gitlab:") else "#"
    return f"{pr['repo'].rsplit('/', 1)[-1]}{sep}{pr['number']}"


def _render_events(events: list[tuple[str, str]], prs: dict) -> str:
    by_pr: dict[str, list[str]] = {}
    for key, line in events:
        by_pr.setdefault(key, []).append(line)
    blocks = []
    for key, lines in by_pr.items():
        pr = prs[key]
        blocks.append(f"**[{_short(pr)}]({pr['url']})** {pr['title']}\n"
                      + "\n".join(f"- {x}" for x in lines))
    return "\n\n".join(blocks)


def _needs_me(pr: dict) -> bool:
    return pr["ci"] == "fail" or pr["review"] == "changes" or pr["merge"] in ("conflict", "rebase")


def _table(prs: dict) -> str:
    if not prs:
        return t("cr.none")
    rows = sorted(prs.values(), key=lambda p: (not _needs_me(p), p["repo"], -p["number"]))
    lines = [t("cr.head"), "|---|---|---|---|"]
    for p in rows:
        merge = t(MERGE[p["merge"]]) if p["merge"] in MERGE else ""
        status = " ".join(x for x in (REVIEW[p["review"]], merge) if x) or "-"
        title = p["title"] if len(p["title"]) <= 40 else p["title"][:39] + "…"
        lines.append(f"| [{_short(p)}]({p['url']}) | {CI[p['ci']]} | {status} | {title} |")
    n = sum(map(_needs_me, prs.values()))
    return "\n".join(lines) + t("cr.foot", n=len(prs), k=n)
