"""Real Web Push from the buddy to the owner's browsers, even with no tab open (the owner,
2026-11-12: "for ours: the 🔔, no push hook, and real web push"; "disable web push by default").

Off unless `[notify] webpush = "ends"` (or "all") in buddy.toml. Then the workbench's 🔔
subscribes that browser; each event that needs the owner goes to every subscribed browser.

How it works (the standards, with pycryptodome, which the buddy already has through lark-oapi):

- VAPID (RFC 8292): one P-256 key pair of this buddy, made once, in state/webpush-vapid.json
  (chmod 600). Its public key is handed to the browser when it subscribes; every push carries
  `Authorization: vapid t=<JWT signed ES256>, k=<public key>`, so the push service (Google's,
  Mozilla's, Apple's) knows it comes from the server that browser subscribed to.
- A subscription (W3C Push API): the browser makes its own P-256 key pair and a 16-byte auth
  secret, keeps the private key, and sends {endpoint, keys: {p256dh, auth}} to the buddy (POST
  /api/push/subscribe). Every browser, profile or machine has its own. Kept in
  state/webpush-subs.json; an endpoint that answers 404 or 410 is gone and is dropped.
- Encryption (RFC 8291, aes128gcm): per message a new P-256 key pair; ECDH with the browser's
  p256dh; HKDF with its auth secret; AES-128-GCM. Only that browser can read it; the push service
  sees its size and time. Tested against the RFC's own example (Appendix A), byte for byte.
- Sending (RFC 8030): one HTTPS POST to the subscription's endpoint, TTL one day.

The channel owns the pseudo-chat "webpush:", added to the owner's linked chats only when it is
on; notify.Quiet holds it to [notify] webpush like any chat; it never gets mirrors.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

CHAT = "webpush:"
SUBJECT = "https://github.com/congzhangzh/brahman"  # VAPID's contact (a URL, so no e-mail goes out)
RS = 4096
log = logging.getLogger("buddy.webpush")
_lock = threading.Lock()


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def unb64u(s: str) -> bytes:
    s = re.sub(r"\s", "", s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _ecdh(private, public_raw: bytes) -> bytes:
    from Crypto.Protocol.DH import key_agreement
    from Crypto.PublicKey import ECC
    pub = ECC.import_key(public_raw, curve_name="P-256")
    return key_agreement(static_priv=private, static_pub=pub, kdf=lambda z: z)


def encrypt(plaintext: bytes, p256dh: str, auth: str, *, as_private=None, salt: bytes | None = None) -> bytes:
    """RFC 8291 aes128gcm: the body of one push message for one subscription. as_private and salt
    are random unless given (the tests give the RFC's)."""
    from Crypto.Cipher import AES
    from Crypto.PublicKey import ECC
    ua_public, auth_secret = unb64u(p256dh), unb64u(auth)
    as_private = as_private or ECC.generate(curve="P-256")
    as_public = as_private.public_key().export_key(format="raw")
    salt = salt or os.urandom(16)
    ecdh = _ecdh(as_private, ua_public)
    prk_key = _hmac(auth_secret, ecdh)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    c = AES.new(cek, AES.MODE_GCM, nonce=nonce)
    body, tag = c.encrypt_and_digest(plaintext + b"\x02")  # one record: the padding delimiter, no padding
    return salt + struct.pack(">I", RS) + bytes([len(as_public)]) + as_public + body + tag


# VAPID: this buddy's key pair

def vapid_path(state: Path) -> Path:
    return state / "webpush-vapid.json"


def vapid(state: Path):
    """This buddy's VAPID private key (made once)."""
    from Crypto.PublicKey import ECC
    p = vapid_path(state)
    with _lock:
        try:
            return ECC.import_key(json.loads(p.read_text())["private_pem"])
        except (OSError, ValueError, KeyError):
            key = ECC.generate(curve="P-256")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"private_pem": key.export_key(format="PEM"),
                                     "public": b64u(key.public_key().export_key(format="raw"))}))
            p.chmod(0o600)
            return key


def public_key(state: Path) -> str:
    """The applicationServerKey a browser subscribes with: the raw P-256 point, base64url."""
    return b64u(vapid(state).public_key().export_key(format="raw"))


def vapid_header(state: Path, endpoint: str, now: float | None = None) -> str:
    """Authorization for one push service: a JWT (ES256) for the endpoint's origin, 12 h."""
    from Crypto.Hash import SHA256
    from Crypto.Signature import DSS
    u = urllib.parse.urlparse(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": SUBJECT}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    key = vapid(state)
    sig = DSS.new(key, "fips-186-3", encoding="binary").sign(SHA256.new(f"{head}.{body}".encode()))  # r || s
    return f"vapid t={head}.{body}.{b64u(sig)}, k={b64u(key.public_key().export_key(format='raw'))}"


# Subscriptions: one per browser

def subs_path(state: Path) -> Path:
    return state / "webpush-subs.json"


def subs(state: Path) -> list[dict]:
    try:
        d = json.loads(subs_path(state).read_text())
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(state: Path, rows: list[dict]) -> None:
    p = subs_path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=1))
    p.chmod(0o600)


def check_sub(sub) -> str:
    """"" when it is a subscription as the Push API gives it, else what is wrong."""
    if not isinstance(sub, dict):
        return "a subscription is an object"
    ep, keys = sub.get("endpoint"), sub.get("keys") or {}
    if not (isinstance(ep, str) and ep.startswith("https://") and len(ep) < 2000):
        return "endpoint: an https URL"
    try:
        if len(unb64u(keys.get("p256dh", ""))) != 65 or len(unb64u(keys.get("auth", ""))) != 16:
            return "keys: p256dh (65 bytes) and auth (16 bytes), base64url"
    except (ValueError, TypeError):
        return "keys: base64url"
    return ""


def subscribe(state: Path, sub: dict, agent: str = "") -> str:
    why = check_sub(sub)
    if why:
        return why
    with _lock:
        rows = [r for r in subs(state) if r["endpoint"] != sub["endpoint"]]
        rows.append({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]},
                     "agent": agent[:120], "added": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        _save(state, rows)
    return ""


def describe(sub: dict) -> dict:
    """A subscribed browser as the Setup tab lists it: which browser on what, the push service, since when."""
    a = sub.get("agent") or ""
    browser = ("Edge" if "Edg/" in a else "Chrome" if "Chrome/" in a else "Firefox" if "Firefox/" in a
               else "Safari" if "Safari/" in a else "a browser")
    on = ("iPhone" if "iPhone" in a else "iPad" if "iPad" in a else "Mac" if "Macintosh" in a else "Android" if "Android" in a
          else "Windows" if "Windows" in a else "Linux" if "Linux" in a else "?")
    return {"browser": f"{browser} on {on}", "host": urllib.parse.urlparse(sub["endpoint"]).netloc, "added": sub.get("added", "")}


def unsubscribe(state: Path, endpoint: str) -> bool:
    with _lock:
        rows = subs(state)
        left = [r for r in rows if r["endpoint"] != endpoint]
        _save(state, left)
    return len(left) != len(rows)


def send(state: Path, title: str, text: str, link: str = "", tag: str = "", post=None) -> dict:
    """One event to every subscribed browser: {sent, failed, dropped}. A 404/410 drops it."""
    payload = json.dumps({"title": title[:200], "body": text[:1500], "link": link, "tag": tag},
                         ensure_ascii=False).encode()
    post = post or _post
    out = {"sent": 0, "failed": 0, "dropped": 0}
    for s in subs(state):
        body = encrypt(payload, s["keys"]["p256dh"], s["keys"]["auth"])
        status = post(s["endpoint"], body, {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                                            "TTL": "86400", "Authorization": vapid_header(state, s["endpoint"])})
        if 200 <= status < 300:
            out["sent"] += 1
        elif status in (404, 410):
            unsubscribe(state, s["endpoint"])
            out["dropped"] += 1
        else:
            out["failed"] += 1
            log.warning("web push to %s: HTTP %s", urllib.parse.urlparse(s["endpoint"]).netloc, status)
        from . import notify
        notify.count(state, "webpush")
    return out


def _post(url: str, body: bytes, headers: dict) -> int:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


class WebPush:
    """The channel: what would be drawn in a chat becomes one notification per browser."""
    name = "webpush"
    mirrors = False

    def __init__(self, state: Path, web: str = "http://127.0.0.1:8787/", post=None):
        self.state, self.web, self.post = state, web, post
        self.n = 0

    def owns(self, chat: str) -> bool:
        return chat == CHAT

    def listen(self, on_message: Callable) -> None:
        while True:
            time.sleep(3600)

    def _event(self, title: str, text: str) -> bool:
        m = re.search(r"#(\d+)", title)
        link = self.web + (f"#story={m[1]}" if m else "")
        r = send(self.state, title, re.sub(r"[*_`]", "", text), link, tag=f"story-{m[1]}" if m else "", post=self.post)
        return r["failed"] == 0

    def track(self, chat, markdown, *, title, handle=None, failed=False, done=False, buttons=()) -> str | None:
        self._event(title, markdown.split("\n", 1)[0] if not (done or failed) else markdown.splitlines()[-1] if markdown else "")
        if handle:
            return handle
        self.n += 1
        return f"webpush-{self.n}"

    def ask(self, chat, markdown, *, title, options=(), ask_id="", failed=False, note="", stop=True) -> str | None:
        self._event("❓ " + title, markdown)
        self.n += 1
        return f"webpush-{self.n}"

    def post(self, chat, markdown, *, title, subtitle="", failed=False, note="", thread="") -> bool:
        return self._event(("⚠️ " if failed else "") + title, markdown)

    def settle(self, chat, handle, markdown, **kw) -> bool:
        return True

    def open_topic(self, chat, topic):
        return None

    def stop_button(self, chat, markdown) -> bool:
        return True

    def prompt(self, chat, text):
        return None

    def reply(self, msg, text) -> None:
        pass

    def progress(self, msg, text, handle=None):
        return None

    def answer(self, msg, handle, markdown, **kw) -> None:
        pass


def mode(cfg: dict) -> str:
    """[notify] webpush: off (the default) | ends | all."""
    from . import notify
    m = str((cfg.get("notify") or {}).get("webpush", "off"))
    return m if m in notify.MODES else "off"


def channel(cfg: dict, state: Path):
    """The web push channel wrapped in its mode. Always there: [notify] webpush is followed live
    (notify.watch), so "off" (the default) only means nothing is sent."""
    m = mode(cfg)
    from . import notify
    port = (cfg.get("web") or {}).get("port", 8787)
    return notify.Quiet(WebPush(state, f"http://127.0.0.1:{port}/"), m, "webpush")
