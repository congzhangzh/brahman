"""Who may use the workbench: the key slots, the one way in (the owner, 2026-11-26: "yes, remove the web token and
web hosts").

The workbench binds the loopbacks only (127.0.0.1 and ::1) and answers only a loopback Host name; a request from
another user on the machine is refused (web._owner). Every request but the public files needs the owner's login:

- **The key slots** ("allow multikey? … key with name so it can be noted, like zfs keyslots"):
  ~/.config/brahman/owner.d/<name>.sha256, one per device or browser, made by `buddy auth new --append <name>` (or a
  device's first setup) in the owner's own shell. A slot holds only the SHA-256 hex of its secret: a request's
  secret (the login form, or `Authorization: Bearer <secret>`) is hashed and compared in constant time with every
  slot, so a hash is no credential; a wrong one waits a second. The cookie is an HMAC of the slot's hash under a
  random key kept in the buddy's state (0600): the hash file alone forges no cookie, and a slot replaced or
  removed ends its cookies, no other. HttpOnly, SameSite=Lax (a notification's click is a navigation, so Strict
  would drop it; POSTs still need X-Buddy, which no other site can send).
- **No slot: closed** (fail closed): the page says what to run, and nothing else is answered.
- Public: the manifest, the icons and the Service Worker (code and pictures; iOS fetches the manifest without
  cookies), and /api/health's works-or-not.
- The event stream's device tokens (events.py) read GET /api/events and nothing else.

The trust root is the first SSH key: owner_dir(). BUDDY_WEB_TOKEN and [web] hosts are read by nothing (since
2026-11-26); doctor says to remove a leftover one.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from pathlib import Path

PUBLIC = {"/manifest.webmanifest", "/sw.js", "/icon-180.png", "/icon-192.png", "/icon-512.png",
          "/api/health"}  # health: {ok, instance} only without a login (web.py)
LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")


OWNER_DIR = "owner.d"  # the key slots: <name>.sha256 (+ <name>.note), like ZFS / LUKS key slots (the owner, 2026-11-26)
OWNER_COOKIE = "buddy_owner"
SLOT = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")


def owner_dir() -> Path:
    """~/.config/brahman/owner.d: the key slots, the only owner login (the owner, 2026-11-26: "allow multikey? …
    key with name so it can be noted, like zfs keyslots"). A slot is a name and the SHA-256 of its secret
    (<name>.sha256, one 64-hex line, 0600; the folder 0700), with an optional <name>.note; a retired slot is
    <name>.sha256.bak-<time>, never read. Only the owner's own shell on this machine makes them (`buddy auth`,
    or a device's first setup through the init key): no web or chat route adds one.

    The trust root is the first SSH key (the init key): whoever holds it sets the system up and adds devices. It is
    the system's weak point by design (the owner, 2026-11-26: "we trust the first ssh key to initialize system
    and it's the weak [point of the] system by design, which simplifies all the system"); no second root, no
    recovery beyond it."""
    from . import install
    return install.config_dir() / OWNER_DIR


def slot_files() -> list[Path]:
    """The slots' hash files: regular files named *.sha256 (a link is not one: it opens nothing)."""
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
    """A key slot exists: the owner check is on, checked first, loopback only. A slot that holds no good hash
    still counts: it closes the buddy to the loopback and opens nothing."""
    return bool(slot_files())


def owner_hashes() -> list[str]:
    """Every hash that logs in, read at each check (bad ones left out)."""
    return list(dict.fromkeys(h for h in map(_hex, slot_files()) if h))


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
    """The secret itself (never its hash): its SHA-256 equals a slot's."""
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


def owner_cookie_value(h: str) -> str:
    """The owner cookie for one slot's hash: that slot replaced or removed, the cookie ends."""
    return hmac.new(_cookie_key(), b"brahman owner cookie v1 " + h.encode(), hashlib.sha256).hexdigest() if h else ""


def required() -> bool:
    """Every request but the public files needs a secret: always (fail closed)."""
    return True


NO_SLOT = "no key slot yet: run `buddy auth new --append <name>` on this machine"


def ok(headers) -> bool:
    """A key slot's cookie, or Authorization: Bearer <a slot's secret>. No slot at all: closed (the owner,
    2026-11-25: "simple by design, no fallback"): `buddy serve` runs (scheduler, duties, chats) and the
    workbench says NO_SLOT."""
    if not owner_on():
        return False
    wants = [owner_cookie_value(h) for h in owner_hashes()]
    for part in (headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == OWNER_COOKIE and any(hmac.compare_digest(v.strip().encode(), w.encode()) for w in wants if w):
            return True
    auth = headers.get("Authorization") or ""
    return auth.startswith("Bearer ") and owner_ok(auth[7:])


def login(given: str) -> str | None:
    """A secret typed into the login form: the Set-Cookie for its slot, or None after a second's wait (no fast
    guessing)."""
    h = owner_match(given) if owner_on() else ""
    if h:
        return f"{OWNER_COOKIE}={owner_cookie_value(h)}; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000"
    time.sleep(1)
    return None


# The verbs (`buddy auth`): the owner's own shell only

def slot_path(name: str) -> Path:
    if not SLOT.fullmatch(name or ""):
        raise ValueError(f"a slot's name: a-z, 0-9 and -, at most 48, starting with a letter or digit (not {name!r})")
    return owner_dir() / f"{name}.sha256"


def _retire(p: Path) -> Path:
    """A slot's file put aside as <file>.bak-<time> (never read again); returns where it went."""
    old = p.with_name(f"{p.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    n = 1
    while old.exists():
        n += 1
        old = p.with_name(f"{p.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-{n}")
    p.rename(old)
    return old


def _write(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)


def add_slot(name: str, note: str = "", replace: bool = False) -> tuple[str, Path, Path | None]:
    """A new secret in slot `name` (256 random bits; only its SHA-256 is written). Returns the secret (to show
    once), the slot's file, and where its old file went (replace). --append refuses a name in use; --replace a
    name not in use."""
    import secrets
    from . import install
    p = slot_path(name)
    if p.exists() and not replace:
        raise ValueError(f"slot {name} exists: buddy auth new --replace {name}")
    if replace and not p.exists():
        raise ValueError(f"no slot {name}: buddy auth new --append {name}")
    install._folder(p.parent)
    kept = _retire(p) if p.exists() else None
    secret = "bos_" + secrets.token_urlsafe(32)
    _write(p, hashlib.sha256(secret.encode()).hexdigest() + "\n")
    if note:
        _write(p.with_suffix(".note"), note.replace("\n", " ").strip()[:200] + "\n")
    return secret, p, kept


def replace_all(name: str, note: str = "") -> tuple[str, Path, list[Path]]:
    """The panic button: every slot retired, then one new slot `name`. Every device sets up again."""
    gone = [_retire(p) for p in slot_files()]
    gone += [_retire(n) for n in sorted(owner_dir().glob("*.note")) if not n.with_suffix(".sha256").exists()]
    secret, p, _ = add_slot(name, note)
    return secret, p, gone


def remove_slot(name: str) -> Path | None:
    """Retire one slot (a lost device): its secret stops at once. Returns where its file went, None if none."""
    p = slot_path(name)
    if not p.exists():
        return None
    note = p.with_suffix(".note")
    if note.exists():
        _retire(note)
    return _retire(p)


def slots() -> list[dict]:
    """{name, at, note} per slot, never a hash or a secret; at: when its secret was made (the file's time)."""
    out = []
    for p in slot_files():
        note = p.with_suffix(".note")
        try:
            text = note.read_text(encoding="utf-8").strip() if note.exists() else ""
        except OSError:
            text = ""
        out.append({"name": p.name.removesuffix(".sha256"), "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
                    "note": text, "ok": bool(_hex(p))})
    return out


def host_of(header: str) -> str:
    """The Host header without its port ("[::1]:8787" → "[::1]", "192.0.2.5:18787" → "192.0.2.5")."""
    h = (header or "").strip().lower()
    if h.startswith("["):
        return h[:h.index("]") + 1] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_ok(header: str) -> bool:
    """May a browser that used this Host reach the workbench? A loopback name only."""
    return host_of(header) in LOOPBACK


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
    """The form asks for the owner secret (a key slot's; the owner, 2026-11-25: "one simple shared secret"). No
    slot yet: no form, only what to run (nothing could get in)."""
    import html
    if not owner_on():
        return (LOGIN.split("<form", 1)[0] + f"<p style='max-width:420px;line-height:1.5'><b>brahman workbench</b><br>"
                f"{html.escape(NO_SLOT)}, then reload this page.</p></body></html>").encode()
    what = "owner secret"
    ask = "The owner secret: this device's key slot, the same one for every space (buddy auth list)."
    return (LOGIN.replace("{ask}", ask).replace("{what}", what).replace("{next}", html.escape(next_path or "/"))
            .replace("{bad}", f"<p style='color:#e66'>not the {what}</p>" if bad else "").encode())
