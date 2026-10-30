"""Draft a rebase of one of my PRs in a scratch worktree; push it only after a yes.

Nothing here writes code. A rebase replays my own commits on the newer base; where they
conflict, git's own strategies decide, and only the one a person picked:

    mine      -X theirs   in a rebase "theirs" is the commit being replayed: mine
    upstream  -X ours     "ours" is the base being rebased onto: upstream's
    myself    leave the worktree mid-rebase, for a person to resolve

The draft lives in its own worktree (state/rebase/<pr>), added to a local clone of the
repository, so none of my own worktrees is touched. The head is fetched from the upstream
repository (pull/N/head, merge-requests/N/head), which works for any fork. Pushing goes to
the fork, with --force-with-lease on the head the draft started from, so a branch that
moved meanwhile is never overwritten. Only repositories listed in push_repos are pushed;
for any other, the answer is the command to push by hand.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

STRATEGY = {"mine": "theirs", "upstream": "ours"}


class Error(Exception):
    pass


def git(cwd: Path, *args: str, check: bool = True) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=600,
                       env=env | {"GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"})
    if check and p.returncode:
        raise Error(f"git {' '.join(args[:3])}: {(p.stderr or p.stdout).strip()[-400:]}")
    return p.stdout.strip()


def _is_ancestor(repo: Path, a: str, b: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", a, b],
                          capture_output=True).returncode == 0


def urls(pr: dict, template: str = "") -> tuple[str, str, str]:
    """(fetch URL of the upstream repo, the head's ref there, push URL of the fork)."""
    host, path = ("github.com", pr["repo"]) if pr["key"].startswith("github:") else pr["repo"].split("/", 1)
    fork = pr.get("head_repo") or ""
    fork_path = fork if pr["key"].startswith("github:") else fork.split("/", 1)[-1]
    ref = (f"refs/pull/{pr['number']}/head" if pr["key"].startswith("github:")
           else f"refs/merge-requests/{pr['number']}/head")
    if template:  # tests: "file:///tmp/x/{host}/{path}.git"
        return template.format(host=host, path=path), ref, template.format(host=host, path=fork_path)
    return f"https://{host}/{path}.git", ref, f"git@{host}:{fork_path}.git"


def find_clone(pr: dict, roots: list[str]) -> Path | None:
    """A local clone with a remote pointing at the PR's repository (or my fork of it)."""
    names = {pr["repo"].split("/", 1)[-1] if pr["key"].startswith("gitlab:") else pr["repo"]}
    if pr.get("head_repo"):
        names.add(pr["head_repo"].split("/", 1)[-1] if pr["key"].startswith("gitlab:") else pr["head_repo"])
    pattern = re.compile("|".join(rf"[:/]{re.escape(n)}(\.git)?/?$" for n in names), re.IGNORECASE)
    for root in roots:
        for d in sorted(Path(root).expanduser().glob("*/")):
            if not (d / ".git").is_dir():  # a main clone, not one of its worktrees
                continue
            remotes = git(d, "config", "--get-regexp", r"^remote\..*\.url$", check=False)
            if any(pattern.search(line.split()[-1]) for line in remotes.splitlines() if line):
                return d
    return None


def draft(pr: dict, clone: Path, where: Path, choice: str = "", template: str = "") -> dict:
    """Rebase the PR's head onto its base in a fresh worktree at `where`.

    Returns {"status": "clean" | "conflict" | "current" | "left", ...}: clean has the new
    head and a summary; conflict lists the files; left is a worktree mid-rebase.
    """
    fetch_url, head_ref, _ = urls(pr, template)
    git(clone, "fetch", "--no-tags", "-q", fetch_url, f"refs/heads/{pr['base']}")
    base = git(clone, "rev-parse", "FETCH_HEAD")
    git(clone, "fetch", "--no-tags", "-q", fetch_url, head_ref)
    head = git(clone, "rev-parse", "FETCH_HEAD")
    out = {"base": base, "old": head, "wt": str(where), "clone": str(clone)}
    if _is_ancestor(clone, base, head):  # already on top of its base: nothing to rebase
        return out | {"status": "current"}
    remove(clone, where)
    where.parent.mkdir(parents=True, exist_ok=True)
    git(clone, "worktree", "add", "--detach", "-q", str(where), head)
    args = ["rebase", "-q"] + (["-X", STRATEGY[choice]] if choice in STRATEGY else []) + [base]
    try:
        git(where, *args)
    except Error:
        files = git(where, "diff", "--name-only", "--diff-filter=U", check=False).split()
        if choice == "myself":
            return out | {"status": "left", "files": files}
        git(where, "rebase", "--abort", check=False)
        remove(clone, where)
        return out | {"status": "conflict", "files": files}
    new = git(where, "rev-parse", "HEAD")
    commits = git(where, "log", "--oneline", "--no-decorate", f"{base}..{new}").splitlines()
    stat = git(where, "diff", "--shortstat", head, new) or "no change in content"
    return out | {"status": "clean", "new": new, "commits": commits, "stat": stat}


def push(pr: dict, d: dict, template: str = "") -> str:
    """Force-push the draft to the PR's branch, only if the branch is still where it was."""
    _, _, push_url = urls(pr, template)
    git(Path(d["wt"]), "push", "-q", f"--force-with-lease=refs/heads/{pr['branch']}:{d['old']}",
        push_url, f"{d['new']}:refs/heads/{pr['branch']}")
    remove(Path(d["clone"]), Path(d["wt"]))
    return push_url


def push_command(pr: dict, d: dict, template: str = "") -> str:
    _, _, push_url = urls(pr, template)
    return (f"git -C {d['wt']} push --force-with-lease=refs/heads/{pr['branch']}:{d['old'][:12]} "
            f"{push_url} {d['new'][:12]}:refs/heads/{pr['branch']}")


def remove(clone: Path, where: Path) -> None:
    if where.exists():
        git(clone, "worktree", "remove", "--force", str(where), check=False)
        shutil.rmtree(where, ignore_errors=True)
    git(clone, "worktree", "prune", check=False)
