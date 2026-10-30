"""Fetch pull/merge request state from GitHub (through gh) and any GitLab (anonymous API).

Every PR comes back as a plain dict, the same shape for both forges:

    key      "github:owner/repo#12" or "gitlab:host/group/project!34"
    repo     "owner/repo" or "host/group/project"
    number, title, url, branch, author
    head_repo, base, head_sha   where the branch lives (my fork), the branch it targets, its tip
    state    OPEN | MERGED | CLOSED
    ci       pass | fail | running | none
    review   approved | changes | -
    merge    ok | conflict | rebase | -
    talk     comments and reviews by people other than the author, oldest first:
             [{"at": iso time, "by": login, "url": ..., "text": first line,
               "body": its main content (_gist), "path": the file, for a review thread's comment}]
"""

import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

GITHUB_URL = re.compile(r"https://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)")
GITLAB_URL = re.compile(r"https://([^/\s]+)/(.+?)/-/merge_requests/(\d+)")


def parse_url(url: str) -> str | None:
    """A PR/MR web URL -> its key, or None."""
    if m := GITHUB_URL.match(url):
        return f"github:{m[1]}#{m[2]}"
    if m := GITLAB_URL.match(url):
        return f"gitlab:{m[1]}/{m[2]}!{m[3]}"
    return None


def split_key(key: str) -> tuple[str, str, int]:
    forge, rest = key.split(":", 1)
    repo, n = re.split(r"[#!](?=\d+$)", rest)
    return forge, repo, int(n)


def _first_line(s: str | None) -> str:
    s = (s or "").strip().splitlines()
    return s[0][:120] if s else ""


GIST = 400  # characters of a comment shown on the card


def _gist(s: str | None, limit: int = GIST) -> str:
    """A comment's main content on one line: without what it quotes (> lines), what it folds
    away (<details>), HTML comments and tags, tables, heading marks and fences (a
    ```suggestion block keeps its code), cut at a word near limit."""
    s = re.sub(r"<!--.*?-->|<details>.*?</details>", "", s or "", flags=re.S | re.I)
    s = re.sub(r"</?[a-zA-Z][^>]*>", "", s)
    lines = []
    for line in s.splitlines():
        line = line.strip()
        if line.startswith((">", "|")):
            continue
        if line.startswith("```"):
            line = "suggestion:" if line[3:].strip() == "suggestion" else ""
        line = re.sub(r"^#+\s+", "", line)
        if line:
            lines.append(line)
    s = " ".join(lines).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0] if " " in s[limit // 2:limit] else s[:limit]
    return cut.rstrip(" ,;:.") + " …"


# GitHub: one GraphQL call for every open PR of mine plus any extra PR by key.

GH_FIELDS = """
fragment P on PullRequest {
  number title url state headRefName headRefOid baseRefName headRepository { nameWithOwner }
  author { login } repository { nameWithOwner }
  mergeable reviewDecision
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
  comments(last: 30) { nodes { author { __typename login } createdAt url body } }
  reviews(last: 30) { nodes { author { __typename login } submittedAt url body state } }
  reviewThreads(last: 50) { nodes { path comments(last: 10) { nodes { author { __typename login } createdAt url body } } } }
}"""

GH_CI = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail", "PENDING": "running", "EXPECTED": "running"}
GH_REVIEW = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes"}


def github(extra: list[str], ignore: set[str] = frozenset()) -> dict[str, dict]:
    """My open PRs on GitHub, plus the PRs named in extra (keys), whatever their state."""
    parts = ['mine: search(query: "is:pr is:open author:@me archived:false", type: ISSUE, first: 100)'
             " { nodes { ...P } }"]
    for i, key in enumerate(extra):
        _, repo, n = split_key(key)
        owner, name = repo.split("/", 1)
        parts.append(f"x{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)})"
                     f" {{ pullRequest(number: {n}) {{ ...P }} }}")
    data = _gh_graphql("query {\n" + "\n".join(parts) + "\n}" + GH_FIELDS)
    nodes = list(data["mine"]["nodes"])
    nodes += [v["pullRequest"] for k, v in data.items() if k != "mine" and v and v.get("pullRequest")]
    return {pr["key"]: pr for pr in (_github_pr(n, ignore) for n in nodes) if pr}


# GitHub has bad moments: a 403 "rate limit exceeded for <ip>" while the token is nearly unused,
# or a 5xx page that is not JSON ("Expecting value"). Both pass within a minute, so try again
# before telling the owner; an answer GitHub means (a repo that is gone) is not tried again.
GH_WAITS = (0, 15, 45)  # seconds before each try
GH_TRANSIENT = re.compile(r"rate limit|HTTP 5\d\d|timed out|timeout|connection reset|EOF", re.I)


def _gh_graphql(query: str) -> dict:
    """The data of one gh api graphql call, or RuntimeError("github: ...") after the last try."""
    for wait in GH_WAITS:
        time.sleep(wait)
        try:
            p = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"],
                               capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            err = "gh timed out after 120 s"
            continue
        try:
            data = json.loads(p.stdout or "{}").get("data")
        except ValueError:  # an HTML error page, not GitHub's JSON
            err = f"not JSON: {_first_line(p.stderr or p.stdout)}"
            continue
        if data:
            return data
        err = (p.stderr or p.stdout).strip()  # gh failed, or a repo in extra is gone and nulled the lot
        if not GH_TRANSIENT.search(err):
            break
    raise RuntimeError(f"github: {err[:300]}")


def _github_pr(n: dict, ignore: set[str]) -> dict | None:
    if not n or "number" not in n:
        return None
    author = (n.get("author") or {}).get("login", "")
    roll = ((n["commits"]["nodes"] or [{}])[0].get("commit") or {}).get("statusCheckRollup")
    talk = [(c["createdAt"], c) for c in n["comments"]["nodes"]]
    talk += [(r["submittedAt"], r) for r in n["reviews"]["nodes"]
             if r.get("submittedAt") and (r.get("body") or r["state"] != "COMMENTED")]
    talk += [(c["createdAt"], c | {"path": t.get("path") or ""})
             for t in n["reviewThreads"]["nodes"] for c in t["comments"]["nodes"]]
    repo = n["repository"]["nameWithOwner"]
    return {
        "key": f"github:{repo}#{n['number']}", "repo": repo, "number": n["number"],
        "title": n["title"], "url": n["url"], "branch": n["headRefName"], "author": author,
        "head_repo": (n.get("headRepository") or {}).get("nameWithOwner", ""),
        "base": n.get("baseRefName", ""), "head_sha": n.get("headRefOid", ""),
        "state": n["state"],
        "ci": GH_CI.get((roll or {}).get("state"), "none"),
        "review": GH_REVIEW.get(n.get("reviewDecision"), "-"),
        # UNKNOWN: GitHub is still working it out (it does so lazily, after a push to either
        # side); "?" keeps the last answer, so a conflict doesn't flap into a fresh card
        "merge": {"CONFLICTING": "conflict", "UNKNOWN": "?"}.get(n.get("mergeable"), "ok"),
        "talk": _talk(talk, author, ignore),
    }


# GitLab: anonymous GraphQL per project (notes included); pipelines need the REST view.

GL_FIELDS = """
fragment M on MergeRequest {
  iid title webUrl state sourceBranch detailedMergeStatus approved author { username }
  notes(last: 50) { nodes { system createdAt author { username } url body } }
}"""

GL_STATE = {"opened": "OPEN", "merged": "MERGED", "closed": "CLOSED", "locked": "OPEN"}
GL_CI = {"success": "pass", "failed": "fail", "running": "running", "pending": "running",
         "created": "running", "canceled": "fail"}


def gitlab(project: str, user: str, extra: list[int],
           ignore: set[str] = frozenset()) -> dict[str, dict]:
    """user's open MRs in one GitLab project ("host/group/project"), plus the MRs numbered in extra."""
    host, path = project.split("/", 1)
    q = [f"open: mergeRequests(authorUsername: {json.dumps(user)}, state: opened, first: 100)"
         " { nodes { ...M } }"]
    q += [f"m{n}: mergeRequest(iid: \"{n}\") {{ ...M }}" for n in extra]
    query = f"query {{ project(fullPath: {json.dumps(path)}) {{\n" + "\n".join(q) + "\n} }" + GL_FIELDS
    d = _post_json(f"https://{host}/api/graphql", {"query": query})
    proj = (d.get("data") or {}).get("project")
    if proj is None:
        raise RuntimeError(json.dumps(d.get("errors"))[:300])  # the caller names the project
    nodes = list(proj["open"]["nodes"]) + [v for k, v in proj.items() if k != "open" and v]
    prs = {}
    for n in nodes:
        author = n["author"]["username"]
        talk = [(x["createdAt"], x) for x in n["notes"]["nodes"] if not x["system"]]
        status = (n.get("detailedMergeStatus") or "").upper()
        pr = {
            "key": f"gitlab:{project}!{n['iid']}", "repo": project, "number": int(n["iid"]),
            "title": n["title"], "url": n["webUrl"], "branch": n["sourceBranch"], "author": author,
            # set from the REST view below: anonymous GraphQL hides a fork's path, and asking
            # for target branch and head here pushes the query over the anonymous complexity
            # limit (gitlab.gnome.org: 237 > 200), which failed every check
            "head_repo": "", "base": "", "head_sha": "",
            "state": GL_STATE.get(n["state"], "OPEN"), "ci": "none",
            "review": "approved" if n.get("approved") else "-",
            "merge": {"NEED_REBASE": "rebase", "CONFLICT": "conflict", "CHECKING": "?",
                      "UNCHECKED": "?"}.get(status, "ok"),
            "talk": _talk(talk, author, ignore),
        }
        prs[pr["key"]] = pr
    # Anonymous GraphQL hides pipelines and forks; the REST view of an MR shows both.
    api = f"https://{host}/api/v4/projects/{urllib.parse.quote(path, safe='')}/merge_requests"
    open_prs = [p for p in prs.values() if p["state"] == "OPEN"]
    with ThreadPoolExecutor(8) as ex:
        views = list(ex.map(lambda p: _get_json(f"{api}/{p['number']}"), open_prs))
        for pr, view in zip(open_prs, views):
            pr["ci"] = GL_CI.get(((view or {}).get("head_pipeline") or {}).get("status"), "none")
            pr["base"], pr["head_sha"] = (view or {}).get("target_branch", ""), (view or {}).get("sha", "")
        forks = {v["source_project_id"] for v in views if v and v.get("source_project_id")
                 and v["source_project_id"] != v.get("project_id")}
        paths = dict(zip(forks, ex.map(lambda i: (_get_json(f"https://{host}/api/v4/projects/{i}")
                                                  or {}).get("path_with_namespace"), forks)))
        for pr, view in zip(open_prs, views):
            sp = (view or {}).get("source_project_id")
            fork = path if sp and sp == view.get("project_id") else paths.get(sp)
            if not fork and sp and user:  # a fork the API won't show anonymously: the usual path
                fork, pr["head_repo_guessed"] = f"{user}/{path.rsplit('/', 1)[-1]}", True
            pr["head_repo"] = f"{host}/{fork}" if fork else ""
    return prs


def _talk(items: list[tuple[str, dict]], author: str, ignore: set[str]) -> list[dict]:
    """What other people said: not the author, not bots, not the logins in ignore."""
    out = []
    for at, c in sorted(items, key=lambda x: x[0]):
        who = c.get("author") or {}
        by = who.get("login") or who.get("username") or "ghost"
        if by != author and by not in ignore and who.get("__typename") != "Bot" \
                and not by.endswith("[bot]"):
            out.append({"at": at, "by": by, "url": c.get("url", ""), "text": _first_line(c.get("body")),
                        "body": _gist(c.get("body"))} | ({"path": c["path"]} if c.get("path") else {}))
    return out


def _post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _get_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None  # a missing pipeline is shown as "none", never an error
