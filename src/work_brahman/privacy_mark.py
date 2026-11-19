#!/usr/bin/env python3
"""The sample `brahman-privacy-mark`: is this story private? Rules only, no model, $0.

The buddy runs it when the owner has no marker of his own. To write your own, copy this file to
a folder on PATH (e.g. ~/bin) as `brahman-privacy-mark`, make it executable and change the
rules: the buddy takes yours from then on. The contract is all there is:

  stdin   JSON: {"kind": "short" | "long", "text": what a person reads of the story (title,
          steps, notes, asks), "words": [privacy] words from buddy.toml, "story": the whole story}
  stdout  one word: private | public
  exit 0  anything else (no word, another exit, over 2 s) counts as private

This sample says private when the text has: the home folder's path or the user's name, this
machine's name, an IPv4 address other than loopback and the documentation ranges (192.0.2.x …),
an e-mail address, a Lark/Feishu or Telegram chat id, a word from [privacy] words, or a pattern
from privacy-scan's list (~/.config/privacy-scan/patterns, one regex per line). Else public.
Only the standard library: it runs under any Python 3.
"""

import getpass
import json
import os
import re
import socket
import sys
from pathlib import Path

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SAFE_IP = re.compile(r"^(127\.|0\.0\.0\.0$|255\.255\.255\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)")  # privacy-scan's
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
IDS = re.compile(r"\b(?:oc|ou|om|on)_[0-9a-f]{16,}\b|\btg:-?\d{5,}\b")  # Lark/Feishu chat, user, message ids; Telegram chats


def patterns() -> list:
    conf = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "privacy-scan" / "patterns"
    try:
        lines = [x.strip() for x in conf.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return []
    out = []
    for x in lines:
        if x and not x.startswith("#"):
            try:
                out.append(re.compile(x))
            except re.error:
                continue  # privacy-scan refuses the list; here one bad line costs only itself
    return out


def why(text: str, words: list) -> str:
    """The first rule that makes the text private, or ""."""
    low = text.lower()
    home, user, host = str(Path.home()), getpass.getuser(), socket.gethostname().split(".")[0]
    if home in text:
        return "the home folder's path"
    if len(user) > 2 and re.search(rf"\b{re.escape(user)}\b", text, re.I):
        return "the user's name"
    if len(host) > 2 and re.search(rf"\b{re.escape(host)}\b", text, re.I):
        return "this machine's name"
    if any(not SAFE_IP.match(ip) for ip in IPV4.findall(text)):
        return "an IP address"
    if EMAIL.search(text):
        return "an e-mail address"
    if IDS.search(text):
        return "a chat id"
    for w in words:
        if str(w).strip() and str(w).lower() in low:
            return "a word from [privacy] words"
    if any(p.search(text) for p in patterns()):
        return "a privacy-scan pattern"
    return ""


def main() -> int:
    try:
        d = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        print("private")
        return 0
    print("private" if why(str(d.get("text", "")), list(d.get("words") or [])) else "public")
    return 0


if __name__ == "__main__":
    sys.exit(main())
