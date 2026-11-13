"""Who may use the workbench (the owner, 2026-11-12: "allow an allowlist? … add basic protection
by a password or token"; "do allowlist and token").

The workbench binds 127.0.0.1 only. Through an SSH forward made with a wildcard bind
(`ssh -L 0.0.0.0:18787:127.0.0.1:8787 …`), another device on the LAN reaches it, and the
connection still arrives from sshd as the owner, so the owner check (web._owner) passes; the
Host check alone stops a browser, not a person who sets the header. Hence:

- BUDDY_WEB_TOKEN in .env (empty: off, everything as before). When set, every request needs it:
  the cookie the buddy sets after `/?token=…` once or the small login page, or
  `Authorization: Bearer <token>` (scripts). The cookie holds an HMAC of the token, never the
  token; HttpOnly, SameSite=Lax (a notification's click is a navigation, so Strict would drop
  it; POSTs still need X-Buddy, which no other site can send). Compared in constant time; a
  wrong token waits a second.
- [web] hosts in buddy.toml: the extra names or addresses a browser may use (e.g.
  "192.168.1.10", "192.168.0.0/16", "mymac.local"). Honoured only while a token is set: an
  allowlist without a password would open the door with nothing behind it.
- Public without the token: the manifest, the icons and the Service Worker: code and pictures
  only, and iOS fetches the manifest without cookies.

Plain HTTP: on the LAN the token and the pages can be read by anyone on that network (doctor
says so). An SSH client on the device itself (`-L` to 127.0.0.1) needs none of this.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import time
import urllib.parse
from pathlib import Path

ENV = "BUDDY_WEB_TOKEN"
COOKIE = "buddy_auth"
PUBLIC = {"/manifest.webmanifest", "/sw.js", "/icon-180.png", "/icon-192.png", "/icon-512.png"}
LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")


_env_cache: dict = {"path": None, "mtime": None, "checked": 0.0, "token": None}


def token(env=None) -> str:
    """BUDDY_WEB_TOKEN, followed live (the owner, 2026-11-12: "make the allowlist work without a
    restart"): the .env beside the watched buddy.toml is read again when it changes, at most once
    a second; without a watched buddy.toml (tests, other commands) the environment says it."""
    if env is not None:
        return (env.get(ENV) or "").strip()
    from . import notify
    cfg_path = notify._watch.get("path")
    if cfg_path is None:
        return (os.environ.get(ENV) or "").strip()
    p = Path(cfg_path).parent / ".env"
    now = time.time()
    if _env_cache["path"] != p or now - _env_cache["checked"] >= 1.0:
        _env_cache.update(path=p, checked=now)
        try:
            m = p.stat().st_mtime
            if m != _env_cache["mtime"] or _env_cache["token"] is None:
                from .configure import _env
                _env_cache.update(mtime=m, token=(_env(p).get(ENV) or "").strip())
        except OSError:
            _env_cache["token"] = (os.environ.get(ENV) or "").strip()
    return _env_cache["token"] or ""


def hosts(start_cfg: dict) -> list[str]:
    """[web] hosts, followed live (notify.config: buddy.toml read again when it changes)."""
    from . import notify
    cfg = notify.config() or start_cfg
    return [str(h) for h in (cfg.get("web") or {}).get("hosts") or []]


def cookie_value(tok: str) -> str:
    return hmac.new(tok.encode(), b"brahman workbench cookie v1", hashlib.sha256).hexdigest()


def host_of(header: str) -> str:
    """The Host header without its port ("[::1]:8787" → "[::1]", "192.168.1.5:18787" → "192.168.1.5")."""
    h = (header or "").strip().lower()
    if h.startswith("["):
        return h[:h.index("]") + 1] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_ok(header: str, hosts: list[str], tok: str) -> bool:
    """May a browser that used this Host reach the workbench? Loopback always; an allowlisted
    name, address or network only while a token is set."""
    h = host_of(header)
    if h in LOOPBACK:
        return True
    if not tok:
        return False
    bare = h.strip("[]")
    for entry in hosts or []:
        e = str(entry).strip().lower()
        if not e:
            continue
        if "/" in e:
            try:
                if ipaddress.ip_address(bare) in ipaddress.ip_network(e, strict=False):
                    return True
            except ValueError:
                continue
        elif e == h or e == bare:
            return True
    return False


def authed(headers, tok: str) -> bool:
    """No token set: yes. Else the cookie (an HMAC of the token) or a Bearer token must match."""
    if not tok:
        return True
    want = cookie_value(tok)
    for part in (headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and hmac.compare_digest(v.strip(), want):
            return True
    auth = headers.get("Authorization") or ""
    return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), tok)


def check(tok: str, given: str) -> bool:
    """A token typed or put in the URL: right or wrong; a wrong one waits a second (no fast guessing)."""
    ok = bool(tok) and hmac.compare_digest(given.strip(), tok)
    if not ok:
        time.sleep(1)
    return ok


def set_cookie(tok: str) -> str:
    return f"{COOKIE}={cookie_value(tok)}; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000"


def strip_token(path: str) -> str:
    """The URL without its token= part (where to go after the cookie is set)."""
    u = urllib.parse.urlsplit(path)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True) if k != "token"]
    return urllib.parse.urlunsplit(("", "", u.path or "/", urllib.parse.urlencode(q), ""))


LOGIN = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>brahman · token</title><style>body{font:16px system-ui,sans-serif;background:#1f1b16;color:#eee;display:grid;place-items:center;min-height:100vh;margin:0}
form{display:grid;gap:10px;padding:24px;border:1px solid #444;border-radius:12px;max-width:320px}input,button{font:inherit;padding:8px;border-radius:8px;border:1px solid #666}
button{background:#c9a227;color:#1f1b16;border:0}p{margin:0;color:#aaa;font-size:13px}</style></head><body>
<form method="post" action="/login"><b>brahman workbench</b><p>This buddy asks for its web token (BUDDY_WEB_TOKEN in its .env).</p>
<input name="token" type="password" autocomplete="current-password" autofocus placeholder="token"><input type="hidden" name="next" value="{next}">
<button>Open</button>{bad}</form></body></html>"""


def login_page(next_path: str = "/", bad: bool = False) -> bytes:
    import html
    return LOGIN.replace("{next}", html.escape(next_path or "/")).replace(
        "{bad}", "<p style='color:#e66'>wrong token</p>" if bad else "").encode()
