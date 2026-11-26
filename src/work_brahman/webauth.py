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
  "192.0.2.10", "192.0.2.0/24", "mymac.local"). Honoured only while a token is set: an
  allowlist without a password would open the door with nothing behind it.
- Public without the token: the manifest, the icons and the Service Worker: code and pictures
  only, and iOS fetches the manifest without cookies.

Plain HTTP: on the LAN the token and the pages can be read by anyone on that network (doctor
says so). An SSH client on the device itself (`-L` to 127.0.0.1) needs none of this.

**The owner secret** (the owner, 2026-11-21: "one secret", "it should all be localhost", "owner.secret
is enough"): when `~/.config/brahman/owner.secret` exists, it is checked first, everywhere (the
workbench, the event stream, the login page, ?token=), and the buddy is loopback only ([web] hosts not
honoured). The file holds only the SHA-256 hex of the secret (`buddy auth new` prints the secret once;
the devices keep it): a request's secret is hashed and compared in constant time, so the hash itself
is no credential. Its cookie is an HMAC of that hash under a random key kept in the buddy's state
(0600), so the hash file alone forges no cookie, and a new secret ends every old cookie. BUDDY_WEB_TOKEN
and the device tokens still work after it (nothing breaks while devices move over); doctor lists them.
Without the file, everything is as before.
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
PUBLIC = {"/manifest.webmanifest", "/sw.js", "/icon-180.png", "/icon-192.png", "/icon-512.png",
          "/api/health"}  # health: {ok} only without the token (web.py)
LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")


_env_cache: dict = {"path": None, "mtime": None, "checked": 0.0, "token": None}


def token(env=None) -> str:
    """BUDDY_WEB_TOKEN, followed live (the owner, 2026-11-12: "make the allowlist work without a
    restart"): buddy.env beside the watched buddy.toml is read again when it changes, at most once
    a second; without a watched buddy.toml (tests, other commands) the environment says it."""
    if env is not None:
        return (env.get(ENV) or "").strip()
    from . import notify
    cfg_path = notify._watch.get("path")
    if cfg_path is None:
        return (os.environ.get(ENV) or "").strip()
    from . import install
    p = install.env_file(Path(cfg_path))
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


OWNER_FILE = "owner.secret"
OWNER_DIR = "owner.d"  # one <device>.sha256 per paired device, beside owner.secret (the owner, 2026-11-25: pairing)
OWNER_COOKIE = "buddy_owner"


def owner_path() -> Path:
    from . import install
    return install.config_dir() / OWNER_FILE


def owner_dir() -> Path:
    """owner.d/ beside owner.secret: each paired device's own secret, as its SHA-256 only (<device>.sha256, one
    64-hex line, 0600), written once at the device's setup through the owner's own SSH key; a lost device:
    delete its file (the owner, 2026-11-25: "share the shared secret", one setup per device)."""
    return owner_path().parent / OWNER_DIR


def device_files() -> list[Path]:
    """The devices' hash files: regular files named *.sha256 (a link is not one: it opens nothing)."""
    try:
        return sorted(p for p in owner_dir().glob("*.sha256") if p.is_file() and not p.is_symlink())
    except OSError:
        return []


def _hex(p: Path) -> str:
    """A file's SHA-256 hex, or "" (unreadable, or not 64 hex digits: nobody gets in by it)."""
    try:
        h = p.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""
    return h if len(h) == 64 and all(c in "0123456789abcdef" for c in h) else ""


def owner_on() -> bool:
    """The owner secret is set up (owner.secret, or a device's file in owner.d/): checked first, loopback only.
    A file that holds no good hash still counts: it closes the buddy to the loopback and opens nothing."""
    return owner_path().exists() or bool(device_files())


def owner_hash() -> str:
    """owner.secret's SHA-256 hex, or "" (no file, unreadable, or not 64 hex digits: nobody gets in by it)."""
    return _hex(owner_path())


def owner_hashes() -> list[str]:
    """Every hash that logs in, read at each check: owner.secret's, then each device's (bad ones left out)."""
    return list(dict.fromkeys(h for h in [owner_hash(), *map(_hex, device_files())] if h))


def owner_match(given: str) -> str:
    """The stored hash the secret's SHA-256 equals, or "". Compared with every one in constant time, never the
    hash itself (it is not the secret)."""
    if not given:
        return ""
    d, hit = hashlib.sha256(given.strip().encode()).hexdigest().encode(), ""
    for h in owner_hashes():
        if hmac.compare_digest(d, h.encode()):
            hit = h
    return hit


def owner_ok(given: str) -> bool:
    """The secret itself (never its hash): its SHA-256 equals owner.secret's or a device's."""
    return bool(owner_match(given))


def _cookie_key() -> bytes:
    """A random key in the buddy's state (0600), made once: the owner cookie's HMAC key."""
    import secrets
    d = Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser() / "work_brahman"
    p = d / "owner-cookie.key"
    try:
        return bytes.fromhex(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        d.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(secrets.token_hex(32), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(p)
        return bytes.fromhex(p.read_text(encoding="utf-8").strip())


def owner_cookie_value(h: str | None = None) -> str:
    """The owner cookie for one stored hash (owner.secret's by default): its file removed or changed, the cookie
    ends."""
    h = owner_hash() if h is None else h
    return hmac.new(_cookie_key(), b"brahman owner cookie v1 " + h.encode(), hashlib.sha256).hexdigest() if h else ""


def required() -> bool:
    """Does a request need a secret? The owner secret set up, or a web token set."""
    return owner_on() or bool(token())


def ok(headers) -> bool:
    """Nothing required: yes. Else the owner first (its cookie, or Bearer <secret>), then the web token."""
    if not required():
        return True
    if owner_on():
        wants = [owner_cookie_value(h) for h in owner_hashes()]
        for part in (headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == OWNER_COOKIE and any(hmac.compare_digest(v.strip().encode(), w.encode()) for w in wants if w):
                return True
        auth = headers.get("Authorization") or ""
        if auth.startswith("Bearer ") and owner_ok(auth[7:]):
            return True
    tok = token()
    return bool(tok) and authed(headers, tok)


def login(given: str) -> str | None:
    """A secret typed or put in the URL: the Set-Cookie for it (the owner's first, then the web token's),
    or None after a second's wait (no fast guessing)."""
    h = owner_match(given) if owner_on() else ""
    if h:
        return f"{OWNER_COOKIE}={owner_cookie_value(h)}; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000"
    tok = token()
    if tok and hmac.compare_digest(given.strip().encode(), tok.encode()):
        return set_cookie(tok)
    time.sleep(1)
    return None


def new_owner_secret() -> tuple[str, Path, Path | None]:
    """A new owner secret: 256 random bits; only its SHA-256 hex is written (0600). Returns the secret
    (shown once), the file, and where an old file was kept (or None)."""
    import secrets
    from . import install
    secret = "bos_" + secrets.token_urlsafe(32)
    p = owner_path()
    install._folder(p.parent)
    kept = install._keep(p) if p.exists() else None
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(hashlib.sha256(secret.encode()).hexdigest() + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)
    return secret, p, kept


def hosts(start_cfg: dict) -> list[str]:
    """[web] hosts, followed live (notify.config: buddy.toml read again when it changes). None while the
    owner secret is set up: then the buddy is loopback only."""
    if owner_on():
        return []
    from . import notify
    cfg = notify.config() or start_cfg
    return [str(h) for h in (cfg.get("web") or {}).get("hosts") or []]


def cookie_value(tok: str) -> str:
    return hmac.new(tok.encode(), b"brahman workbench cookie v1", hashlib.sha256).hexdigest()


def host_of(header: str) -> str:
    """The Host header without its port ("[::1]:8787" → "[::1]", "192.0.2.5:18787" → "192.0.2.5")."""
    h = (header or "").strip().lower()
    if h.startswith("["):
        return h[:h.index("]") + 1] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_ok(header: str, hosts: list[str], tok: str) -> bool:
    """May a browser that used this Host reach the workbench? Loopback always; an allowlisted
    name, address or network only while a token is set, and never while the owner secret is."""
    h = host_of(header)
    if h in LOOPBACK:
        return True
    if not tok or owner_on():
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
        if k == COOKIE and hmac.compare_digest(v.strip().encode(), want.encode()):  # bytes: any text compares
            return True
    auth = headers.get("Authorization") or ""
    return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip().encode(), tok.encode())


def check(tok: str, given: str) -> bool:
    """A token typed or put in the URL: right or wrong; a wrong one waits a second (no fast guessing)."""
    ok = bool(tok) and hmac.compare_digest(given.strip().encode(), tok.encode())
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
<title>brahman · sign in</title><style>body{font:16px system-ui,sans-serif;background:#1f1b16;color:#eee;display:grid;place-items:center;min-height:100vh;margin:0}
form{display:grid;gap:10px;padding:24px;border:1px solid #444;border-radius:12px;max-width:320px}input,button{font:inherit;padding:8px;border-radius:8px;border:1px solid #666}
button{background:#c9a227;color:#1f1b16;border:0}p{margin:0;color:#aaa;font-size:13px}</style></head><body>
<form method="post" action="/login"><b>brahman workbench</b><p>{ask}</p>
<input name="token" type="password" autocomplete="current-password" autofocus placeholder="{what}"><input type="hidden" name="next" value="{next}">
<button>Open</button>{bad}</form>
<script>const n = document.querySelector("[name=next]"); if (location.hash && !n.value.includes("#")) n.value += location.hash;</script>
</body></html>"""


def login_page(next_path: str = "/", bad: bool = False) -> bytes:
    """The form names what it takes: the owner secret while it is set up (the one secret for every space; the
    owner, 2026-11-25: "one simple shared secret"), else the buddy's web token, as before."""
    import html
    what = "owner secret" if owner_on() else "web token"
    ask = ("The owner secret: the one secret for every space (buddy auth new makes it)." if owner_on()
           else "This buddy asks for its web token (BUDDY_WEB_TOKEN).")
    return (LOGIN.replace("{ask}", ask).replace("{what}", what).replace("{next}", html.escape(next_path or "/"))
            .replace("{bad}", f"<p style='color:#e66'>not the {what}</p>" if bad else "").encode())
