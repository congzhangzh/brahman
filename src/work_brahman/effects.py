"""What a step changed outside its session, and whether that can be taken back (specs/views.md, phase 3;
Boss Cong, 2026-11-16: "Views phase 3"). Rules on the command's program and subcommand, by script, $0:
names only (`git push`, `gh release create`), never arguments.

Each rule: a regex on one shell segment ("git push origin main"), the effect's kind, and `reversible`.
Irreversible means it left the machine or can't be undone by the next command alone: a push, a release,
a merged PR, a message sent, a deleted file. The owner's own rules go first, from
~/.config/brahman/effects.json ([{"match": "^mytool deploy\\b", "kind": "deploy", "reversible": false,
"what": "mytool deploy"}]; without "what", only the program is kept).
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

RULES = [
    (r"^git\s+commit\b", "commit", True),
    # a flag before -f/--force, or a +refspec; never "-f" inside a branch name (feature-f)
    (r"^git\s+push\b.*(\s--force|\s-(?!-)[A-Za-z]*f[A-Za-z]*(?=\s|$)|\s\+\S)", "force-push", False),
    (r"^git\s+push\b", "push", False),
    (r"^git\s+merge\b", "merge", True),
    (r"^git\s+rebase\b", "rebase", True),
    (r"^git\s+tag\b", "tag", True),
    (r"^git\s+reset\s+--hard\b", "reset", False),
    (r"^gh\s+release\s+create\b", "release", False),
    (r"^gh\s+pr\s+create\b", "pr", False),
    (r"^gh\s+pr\s+merge\b", "merge-pr", False),
    (r"^gh\s+pr\s+(comment|review)\b", "message", False),
    (r"^gh\s+issue\s+(create|comment)\b", "message", False),
    (r"^glab\s+mr\s+create\b", "pr", False),
    (r"^glab\s+mr\s+merge\b", "merge-pr", False),
    (r"^glab\s+release\s+create\b", "release", False),
    # the verb is systemctl's first word after its flags (never "foo-stop.service")
    (r"^systemctl(\s+-\S+)*\s+(reload-or-restart|try-restart|restart|stop|start|reload|enable|disable|kill|mask)(?=\s|$)",
     "service", True),
    (r"^rm\b.*\s(-(?!-)[A-Za-z]*[rR]|--recursive\b)", "delete", False),
    (r"^(docker|podman)\s+push\b", "publish", False),
    (r"^(npm|uv|twine)\s+publish\b|^twine\s+upload\b", "publish", False),
    (r"^heads\s+publish\b.*--yes", "publish", False),
    (r"^buddy-short\s+(start|mark|note|done|ask)\b", "card", True),
]
MCP = [  # an MCP tool by its name's verb
    (r"__(send|post|reply|forward)\w*$", "message", False),
    (r"__(create|update|patch|delete|remove|merge|publish)\w*$", "change", False),
]


def rules() -> list[tuple[str, str, bool, str | None]]:
    """(match, kind, reversible, what): what is None for a built-in rule (program and subcommand,
    _what); an owner's rule names its own "what", else only its program is kept (an owner's command
    may carry a host or a path in its second word: never stored)."""
    f = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "brahman" / "effects.json"
    own = []
    try:
        for r in json.loads(f.read_text(encoding="utf-8")):
            own.append((str(r["match"]), str(r["kind"]), bool(r.get("reversible", False)), str(r.get("what") or "")))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return own + [(*r, None) for r in RULES]


SKIP = ("cd", "pushd", "popd", "export", "set", "source", ".")
KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}", "(", ")"}
WRAP_ARGS = {  # a wrapper's flags that take the next word
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "--user", "--group"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S"},
}
GIT_ARGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}  # git's own flags before the subcommand


def _split(s: str) -> list[str]:
    """The shell's simple commands, as bash splits them: on && || ; | & and newlines outside quotes
    (a line break separates commands too); a comment and a here-document's body are no command."""
    segs, cur, q, heredocs, i, n = [], [], None, [], 0, len(s)
    while i < n:
        c = s[i]
        if q:  # inside quotes: nothing splits
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                cur.append(s[i + 1])
                i += 1
            elif c == q:
                q = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            if s[i + 1] != "\n":  # a backslash-newline continues the line
                cur += [c, s[i + 1]]
            i += 2
            continue
        if c in "'\"":
            q = c
        elif c == "#" and (not cur or cur[-1].isspace()):  # a comment, to the end of the line
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        elif s.startswith("<<", i) and not s.startswith("<<<", i) and not _in_arith("".join(cur)):
            m = re.match(r"<<(-?)\s*(['\"]?)([\w.-]+)\2", s[i:])
            if m:
                heredocs.append((m.group(3), m.group(1) == "-"))
                cur.append(m.group(0))
                i += m.end()
                continue
        elif c == "&" and ((cur and cur[-1] in "<>") or s.startswith("&>", i)):  # 2>&1, &>file: a redirect
            pass
        elif c in "\n;&|":
            segs.append("".join(cur))
            cur = []
            i += 2 if c in "&|" and s.startswith(c * 2, i) else 1
            if c == "\n":
                for word, tabs in heredocs:  # skip each body to its delimiter's line
                    while i < n:
                        j = s.find("\n", i)
                        line, i = s[i:n if j < 0 else j], n if j < 0 else j + 1
                        if (line.lstrip("\t") if tabs else line) == word:
                            break
                heredocs = []
            continue
        cur.append(c)
        i += 1
    segs.append("".join(cur))
    return [x.strip() for x in segs if x.strip()]


def _in_arith(text: str) -> bool:
    """Inside $((…)), where << is a shift (x=$((1<<3))), not a here-document."""
    return text.count("$((") > text.count("))")


def _words(seg: str) -> list[str]:
    """One command's words from its program on: past keywords, X=1, wrappers with their flags and
    numbers (sudo -u x, timeout 60), and the program's own flags before its subcommand (git -C dir,
    gh -R owner/repo); [] for cd and the like."""
    from .tooluse import WRAPPERS
    try:
        words = shlex.split(seg)
    except ValueError:
        words = seg.split()
    while words:
        w = words[0].lstrip("(")
        if not w or w in KEYWORDS or re.fullmatch(r"\w+=.*", w):
            words = words[1:]
            continue
        if os.path.basename(w) in WRAPPERS:
            takes, words = WRAP_ARGS.get(os.path.basename(w), set()), words[1:]
            while words and (words[0].startswith("-") or re.fullmatch(r"\d+(\.\d+)?[smhd]?", words[0])):
                words = words[2:] if words[0] in takes else words[1:]
            continue
        words[0] = w  # "(cd" is cd
        break
    if not words or words[0] in SKIP:
        return []
    prog, rest = os.path.basename(words[0].lstrip("(")), words[1:]
    if prog == "git":
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in GIT_ARGS else rest[1:]
    if prog in ("gh", "glab"):  # -R owner/repo may stand anywhere
        out, skip = [], False
        for w in rest:
            if skip:
                skip = False
            elif w in ("-R", "--repo"):
                skip = True
            elif not w.startswith("--repo="):
                out.append(w)
        rest = out
    return [prog, *rest]


def _segments(command: str) -> list[str]:
    return [" ".join(w) for w in map(_words, _split(command or "")) if w]


def of_call(tool: str, inp: dict) -> list[dict]:
    """The effects of one tool call: [{kind, what, reversible}], what = program and subcommand only."""
    found = []
    if tool.startswith("mcp__"):
        for rx, kind, rev in MCP:
            if re.search(rx, tool):
                found.append({"kind": kind, "what": tool.split("__", 1)[1].replace("__", " "), "reversible": rev})
                break
        return found
    if tool != "Bash":
        return found
    table = rules()
    for seg in _segments(str(inp.get("command", ""))):
        for rx, kind, rev, what in table:
            if re.search(rx, seg):
                found.append({"kind": kind, "what": _what(seg) if what is None else what or seg.split()[0],
                              "reversible": rev})
                break
    return found


def _what(seg: str) -> str:
    """The program and its subcommand words only: "git push", "gh release create", "systemctl restart", "rm";
    never an argument (a remote, a branch, a unit, a file)."""
    words = [w for w in seg.split() if not w.startswith("-")]
    prog = words[0]
    n = 3 if prog in ("gh", "glab") else 1 if prog in ("rm",) else 2
    keep = [prog]
    for w in words[1:n]:
        if not re.fullmatch(r"[a-z][a-z-]*", w):  # a subcommand is a plain lowercase word
            break
        keep.append(w)
    if prog == "systemctl":  # its verb: the first word after its flags
        keep = [prog, words[1] if len(words) > 1 else ""]
    return " ".join(x for x in keep if x)
