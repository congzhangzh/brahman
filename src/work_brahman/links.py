"""Links for a step's --in / --out values (Boss Cong, 2026-11-15/16: "the details view with URLs that
point to the source"; "your values need the right context, be careful"; "the computer first").

A wrong link is worse than none: he taps it while debugging and lands on another repo's run. So a
value becomes a link only when its repo is certain, decided by script at mark time, $0, no network:

1. a value that already is a URL (http/https) is linked as given;
2. a known key (run, pr, mr, issue, sha, commit, release, tag) is linked when its repo is certain:
   - named in the value (`FreeRDP/FreeRDP#123`, `gitlab.gnome.org/GNOME/gtk#45`) or beside it
     (`repo=owner/name` in the same mark's --in/--out);
   - else the marking checkout has exactly one forge remote (GitHub, or a host with "gitlab" in its
     name). A fork with origin and upstream is not certain (a PR's CI runs upstream, a branch's on
     the fork): no link, and the caller prints why and how to name the repo.
3. The URL is stored in the mark (`links: {key: url}`) and written into the step's event as
   `[key=value](url)`, so a later checkout, a rename or another machine can't change it; the card
   (Telegram, Lark), the page and the terminal only display it.

An unknown forge: plain text. Only github.com and GitLab URL shapes are known.
"""

from __future__ import annotations

import re
import subprocess

KEYS = ("run", "pr", "mr", "issue", "sha", "commit", "release", "tag")
GITHUB = {"run": "actions/runs/{v}", "pr": "pull/{v}", "mr": "pull/{v}", "issue": "issues/{v}",
          "sha": "commit/{v}", "commit": "commit/{v}", "release": "releases/tag/{v}", "tag": "releases/tag/{v}"}
GITLAB = {"run": "-/pipelines/{v}", "pr": "-/merge_requests/{v}", "mr": "-/merge_requests/{v}",
          "issue": "-/issues/{v}", "sha": "-/commit/{v}", "commit": "-/commit/{v}",
          "release": "-/releases/{v}", "tag": "-/tags/{v}"}
VALUE = {"run": r"\d+", "pr": r"\d+", "mr": r"\d+", "issue": r"\d+", "sha": r"[0-9a-f]{7,40}",
         "commit": r"[0-9a-f]{7,40}", "release": r"[\w.+-]+", "tag": r"[\w.+-]+"}


def forge(host: str) -> str | None:
    host = host.lower()
    return "github" if host == "github.com" else "gitlab" if "gitlab" in host else None


def parse_remote(url: str) -> tuple[str, str] | None:
    """(host, owner/name) of a git remote URL: scp-like, ssh:// or https://; None when not a forge."""
    url = url.strip()
    m = (re.match(r"^(?:ssh://)?[\w.-]+@([\w.-]+)(?::\d+)?[:/](.+?)(?:\.git)?/?$", url)
         or re.match(r"^https?://(?:[^@/]+@)?([\w.-]+)(?::\d+)?/(.+?)(?:\.git)?/?$", url))
    if not m or "/" not in m.group(2):
        return None
    host, path = m.group(1).lower(), m.group(2)
    return (host, path) if forge(host) else None


def remotes(cwd: str) -> list[tuple[str, str]]:
    """The checkout's distinct forge remotes, from `git remote -v` (local, no network)."""
    try:
        p = subprocess.run(["git", "-C", cwd, "remote", "-v"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    seen = []
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and (r := parse_remote(parts[1])) and r not in seen:
            seen.append(r)
    return seen


def _repo(text: str, known: list[tuple[str, str]]) -> tuple[str, str] | None:
    """A repo named in a value: a URL, host/owner/name, or owner/name (the host of a remote with that
    path, else github.com, owner/name being GitHub's form)."""
    text = text.strip().strip("/")
    if text.startswith(("http://", "https://")) or text.startswith("git@"):
        return parse_remote(text)
    parts = text.split("/")
    if len(parts) >= 3 and "." in parts[0]:
        host = parts[0].lower()
        return (host, "/".join(parts[1:])) if forge(host) else None
    if len(parts) >= 2 and all(parts):
        return next((h for h, p in known if p.lower() == text.lower()), "github.com"), text
    return None


def url(host: str, path: str, key: str, value: str) -> str | None:
    shape = (GITHUB if forge(host) == "github" else GITLAB if forge(host) == "gitlab" else {}).get(key)
    if not shape or not re.fullmatch(VALUE[key], value):
        return None
    return f"https://{host}/{path}/" + shape.format(v=value)


def resolve(values: dict, cwd: str, beside: dict | None = None) -> tuple[dict, str]:
    """({key: url}, a hint when a known key could not be linked) for one mark's --in/--out values."""
    links, unsure = {}, []
    known = remotes(cwd) if cwd else []
    named = None
    for k, v in {**(beside or {}), **values}.items():
        if k == "repo":
            named = _repo(str(v), known)
    for key, value in values.items():
        value = str(value)
        if re.match(r"^https?://\S+$", value):
            links[key] = value
            continue
        if key not in KEYS:
            continue
        repo, plain = named, value
        if m := re.match(r"^(.+?)[#@](.+)$", value):  # FreeRDP/FreeRDP#123, group/proj@sha
            repo, plain = _repo(m.group(1), known), m.group(2)
        if repo is None and len(known) == 1:
            repo = known[0]
        if repo is None:
            unsure.append(key)
            continue
        if u := url(repo[0], repo[1], key, plain):
            links[key] = u
    hint = ""
    if unsure:
        why = (f"{len(known)} forge remotes ({', '.join(p for _, p in known)})" if len(known) > 1
               else "no GitHub or GitLab remote here")
        hint = (f"no link for {', '.join(unsure)}: {why}, so the repo isn't certain; name it: "
                f"--out {unsure[0]}=owner/name#<value> or --out repo=owner/name")
    return links, hint
