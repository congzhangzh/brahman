"""`buddy peer`: two brahmans pass git bundles to each other, signed with one shared secret.

The owner's words (2026-11-12): "p2p by another build which just uses a shared secret, and a
warning to work only under a restricted environment". This is that build, and only that: the
pairing of specs/multi-brahman.md (pinned keys, trust scopes, a gate around each call) is not it.

    buddy peer serve [--bind 127.0.0.1] [--port 8790]     receive into the inbox
    buddy peer send http://host:8790 <repo> <range> --note "what and why"
    buddy peer inbox                                       what came, newest first

Both sides set BRAHMAN_PEER_SECRET (at least 32 characters, the same on both; e.g.
`python -c "import secrets; print(secrets.token_urlsafe(32))"`). They share their context the
way they already can (a file share, GitHub): the note says where to look.

A request carries X-Peer-From, X-Peer-Time (Unix seconds) and X-Peer-Sig, the HMAC-SHA256 of
"time\\nfrom\\nsha256(body)" under the secret. The receiver refuses a wrong signature (compared in
constant time), a time more than 5 minutes off, a body over the size limit, a request seen before
(the same signature, within the time window) and anything that is not a git bundle. What
passes is only **stored**: inbox/<time>-<from>/{changes.bundle, note.json}. Nothing is applied,
fetched or run; the receiving side's session looks at it (`git bundle verify`, then `git fetch
<bundle> <ref>:<branch>` and its own tests) like any proposal.

**Restricted environment only.** One secret for every peer: whoever has it can send as anyone,
and a peer cannot be dropped without changing it everywhere. The body crosses as plain HTTP:
readable, and the secret only proves it unchanged. So: a LAN you own, a VPN (WireGuard,
Tailscale) or an SSH tunnel, never the open internet. It binds 127.0.0.1 unless told otherwise,
and says this warning each time it starts or sends.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import socket
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENV = "BRAHMAN_PEER_SECRET"
MIN_SECRET = 32
SKEW = 300  # seconds a request's time may be off
MAX_BODY = 50 * 1024 * 1024
BUNDLE_HEAD = (b"# v2 git bundle\n", b"# v3 git bundle\n")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
WARNING = ("⚠️  buddy peer: one shared secret, plain HTTP. Use it only in a restricted environment "
           "(a LAN you own, a VPN, an SSH tunnel), never on the open internet: anyone with the "
           "secret can send as any peer, and anyone on the path can read what is sent.")


def secret(env=os.environ) -> bytes:
    s = env.get(ENV, "")
    if len(s) < MIN_SECRET:
        raise ValueError(f"set {ENV} to the same secret on both sides, at least {MIN_SECRET} characters "
                         "(python -c \"import secrets; print(secrets.token_urlsafe(32))\")")
    return s.encode()


def sign(key: bytes, sender: str, when: int, body: bytes) -> str:
    msg = f"{when}\n{sender}\n{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def check(key: bytes, headers, body: bytes, now: float, seen: dict) -> str:
    """"" when the request may be stored, else why not. `seen`: signatures within the window."""
    why = verify(key, headers, body, now, seen)
    if why:
        return why
    if not body.startswith(BUNDLE_HEAD):
        return "not a git bundle"
    return ""


def verify(key: bytes, headers, body: bytes, now: float, seen: dict) -> str:
    """"" when it is signed with the secret, on time and new, else why not."""
    sender, when, sig = headers.get("X-Peer-From", ""), headers.get("X-Peer-Time", ""), headers.get("X-Peer-Sig", "")
    if not NAME_RE.match(sender):
        return "bad sender name"
    if not when.isdigit() or abs(now - int(when)) > SKEW:
        return "time too far off (clocks more than 5 minutes apart?)"
    if not hmac.compare_digest(sign(key, sender, int(when), body), sig):
        return "bad signature (not the same secret?)"
    for s, t in list(seen.items()):
        if now - t > 2 * SKEW:
            del seen[s]
    if sig in seen:
        return "seen before"
    seen[sig] = now
    return ""


def store(inbox: Path, sender: str, bundle: bytes, note: str, now: float) -> Path:
    base = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{sender}"
    d, n = inbox / base, 1
    while d.exists():  # never write over what came before
        n += 1
        d = inbox / f"{base}.{n}"
    d.mkdir(parents=True)
    (d / "changes.bundle").write_bytes(bundle)
    (d / "note.json").write_text(json.dumps({"from": sender, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                             time.localtime(now)), "note": note}, ensure_ascii=False, indent=1) + "\n")
    return d


def log_path(env=os.environ) -> Path:
    return inbox_dir(env).parent / "peer.jsonl"


def record(event: str, env=os.environ, **fields) -> None:
    """One line in the peer log: what came, what was refused, what was sent, each check."""
    p = log_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields},
                           ensure_ascii=False) + "\n")


def read_log(env=os.environ, last: int = 200) -> list[dict]:
    try:
        lines = log_path(env).read_text().splitlines()[-last:]
    except OSError:
        return []
    out = []
    for x in lines:
        try:
            out.append(json.loads(x))
        except ValueError:
            continue
    return out


def server(bind: str, port: int, inbox: Path, key: bytes, log=print, name: str = "",
           env=os.environ) -> ThreadingHTTPServer:
    seen: dict = {}
    me = name or socket.gethostname().split(".")[0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # its own one line per request below
            pass

        def _answer(self, code: int, text: str) -> None:
            data = (text + "\n").encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path == "/ping":  # a check: answered signed, so the caller knows we hold the secret too
                body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 64))
                why = verify(key, self.headers, body, time.time(), seen)
                sender = self.headers.get("X-Peer-From", "?")
                record("ping refused" if why else "ping", env, peer=sender, ip=self.client_address[0], why=why)
                if why:
                    return self._answer(403, why)
                now = int(time.time())
                data = json.dumps({"name": me, "time": now}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Peer-Sig", sign(key, me, now, self.headers.get("X-Peer-Sig", "").encode() + data))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if self.path != "/bundle":
                return self._answer(404, "only POST /bundle or /ping")
            size = int(self.headers.get("Content-Length") or 0)
            if size <= 0 or size > MAX_BODY:
                return self._answer(413, f"a bundle of 1 byte to {MAX_BODY // 1024 // 1024} MB")
            body = self.rfile.read(size)
            why = check(key, self.headers, body, time.time(), seen)
            sender = self.headers.get("X-Peer-From", "?")
            if why:
                log(f"refused from {self.client_address[0]} ({sender}): {why}")
                record("refused", env, peer=sender, ip=self.client_address[0], why=why, bytes=size)
                return self._answer(403, why)
            note = urllib.parse.unquote(self.headers.get("X-Peer-Note", ""))[:4000]
            d = store(inbox, sender, body, note, time.time())
            log(f"stored from {sender}: {d}")
            record("received", env, peer=sender, ip=self.client_address[0], bytes=size, stored=d.name, note=note[:200])
            self._answer(200, f"stored as {d.name}")

    return ThreadingHTTPServer((bind, port), Handler)


def make_bundle(repo: str, rng: str) -> bytes:
    p = subprocess.run(["git", "-C", repo, "bundle", "create", "-", *rng.split()], capture_output=True)
    if p.returncode != 0:
        raise ValueError(p.stderr.decode(errors="replace").strip() or "git bundle create failed")
    return p.stdout


def send(url: str, sender: str, bundle: bytes, note: str, key: bytes, now: float | None = None) -> str:
    when = int(now if now is not None else time.time())
    req = urllib.request.Request(url.rstrip("/") + "/bundle", data=bundle, method="POST", headers={
        "X-Peer-From": sender, "X-Peer-Time": str(when), "X-Peer-Sig": sign(key, sender, when, bundle),
        "X-Peer-Note": urllib.parse.quote(note), "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode().strip()
    except urllib.error.HTTPError as e:
        raise ValueError(f"refused: {e.read().decode().strip()}") from e


def inbox_dir(env=os.environ) -> Path:
    base = Path(env["XDG_STATE_HOME"]) if env.get("XDG_STATE_HOME") else Path.home() / ".local" / "state"
    return base / "work_brahman" / "peer-inbox"


def main(args) -> int:
    print(WARNING, file=sys.stderr)
    cfg = load()
    if args.action == "inbox":
        d = inbox_dir()
        items = sorted(d.iterdir(), reverse=True) if d.exists() else []
        for x in items:
            n = json.loads((x / "note.json").read_text())
            print(f"{x.name}\t{n['from']}\t{n['note'][:70]}")
        if not items:
            print(f"nothing in {d}")
        return 0
    if args.action == "log":
        for e in read_log(last=args.last):
            print(log_line(e))
        return 0
    if args.action == "doctor":
        rows = doctor(cfg)
        for ok, what, how in rows:
            print(f"{'✅' if ok else '⚠️ ' if ok is None else '❌'} {what}" + (f"\n     {how}" if how else ""))
        return 0 if all(ok is not False for ok, _, _ in rows) else 1
    try:
        key = secret()
        if args.action == "serve":
            bind, port = args.bind or cfg["bind"], args.port or cfg["port"]
            if bind not in ("127.0.0.1", "::1", "localhost"):
                print(f"⚠️  listening on {bind}: every machine that can reach it can try the secret", file=sys.stderr)
            srv = server(bind, port, inbox_dir(), key, log=lambda s: print(s, flush=True), name=cfg["name"])
            print(f"receiving on http://{bind}:{port}/bundle into {inbox_dir()}", flush=True)
            record("serve", bind=bind, port=port)
            srv.serve_forever()
            return 0
        sender = args.name or cfg["name"]
        if not NAME_RE.match(sender):
            raise ValueError("--name: letters, digits, . _ - only, at most 40")
        point = cfg["points"].get(args.url)
        url = point["url"] if point else args.url
        bundle = make_bundle(args.repo, args.range)
        try:
            said = send(url, sender, bundle, args.note, key)
        except ValueError as e:
            record("send failed", peer=args.url, why=str(e), bytes=len(bundle))
            raise
        record("sent", peer=args.url, bytes=len(bundle), range=args.range, note=args.note[:200])
        print(said)
        return 0
    except ValueError as e:
        print(f"buddy peer: {e}", file=sys.stderr)
        return 2


def log_line(e: dict) -> str:
    """One log event as a line: when, what, with whom, the rest."""
    rest = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("at", "event", "peer") and v not in ("", None))
    return f"{e.get('at', '')[:19].replace('T', ' ')}  {e.get('event', ''):<13} {e.get('peer', ''):<12} {rest}".rstrip()

def ping(url: str, sender: str, key: bytes, timeout: float = 5.0) -> dict:
    """Check one peer: {ok, why, name, skew (s), ms}. ok only when it answered signed with our secret."""
    t0 = time.time()
    when, body = int(t0), b"ping " + os.urandom(8).hex().encode()  # never twice the same: no "seen before"
    sig = sign(key, sender, when, body)
    req = urllib.request.Request(url.rstrip("/") + "/ping", data=body, method="POST",
                                 headers={"X-Peer-From": sender, "X-Peer-Time": str(when), "X-Peer-Sig": sig})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data, theirs = r.read(), r.headers.get("X-Peer-Sig", "")
    except urllib.error.HTTPError as e:
        why = e.read().decode(errors="replace").strip()
        return {"ok": False, "why": f"refused: {why}" if why else f"HTTP {e.code}", "ms": int((time.time() - t0) * 1000)}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "why": f"unreachable: {getattr(e, 'reason', e)}", "ms": int((time.time() - t0) * 1000)}
    ms = int((time.time() - t0) * 1000)
    try:
        info = json.loads(data)
        name, their_time = str(info["name"]), int(info["time"])
    except (ValueError, KeyError, TypeError):
        return {"ok": False, "why": "not a buddy peer (its answer is not ours)", "ms": ms}
    if not hmac.compare_digest(sign(key, name, their_time, sig.encode() + data), theirs):
        return {"ok": False, "why": "its answer is not signed with our secret", "name": name, "ms": ms}
    skew = their_time - int(t0 + (time.time() - t0) / 2)
    why = f"clocks {abs(skew)} s apart (over {SKEW} s: sends will be refused)" if abs(skew) > SKEW else ""
    return {"ok": not why, "why": why, "name": name, "skew": skew, "ms": ms}


# The points: the peers this side sends to and checks, with this side's own name and port.
# peers.toml beside buddy.toml; the secret stays in the environment (.env), never in this file.

def config_path(env=os.environ) -> Path:
    base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "work_brahman" / "peers.toml"


def load(env=os.environ) -> dict:
    import tomllib
    try:
        d = tomllib.loads(config_path(env).read_text())
    except (OSError, ValueError):
        d = {}
    points = {k: {"url": str(v.get("url", "")), "note": str(v.get("note", ""))}
              for k, v in (d.get("points") or {}).items() if isinstance(v, dict)}
    return {"name": str(d.get("name") or socket.gethostname().split(".")[0]),
            "bind": str(d.get("bind") or "127.0.0.1"), "port": int(d.get("port") or 8790), "points": points}


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a TOML basic string is a JSON string


def validate(cfg: dict) -> str:
    """"" when it may be saved, else what is wrong."""
    if not NAME_RE.match(cfg["name"]):
        return "this side's name: letters, digits, . _ - only, at most 40"
    if not (0 < int(cfg["port"]) < 65536):
        return "the port: 1 to 65535"
    for k, v in cfg["points"].items():
        if not NAME_RE.match(k):
            return f"peer {k!r}: letters, digits, . _ - only, at most 40"
        u = urllib.parse.urlparse(v["url"])
        if u.scheme not in ("http", "https") or not u.hostname:
            return f"peer {k}: the address is http://host:port"
    return ""


def save(cfg: dict, env=os.environ) -> Path:
    why = validate(cfg)
    if why:
        raise ValueError(why)
    p = config_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        import shutil
        shutil.copy2(p, p.with_name(f"peers.toml.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    lines = ["# buddy peer: this side and the peers it sends to (`buddy tui` and the workbench's Setup tab change it).",
             "# The secret is BRAHMAN_PEER_SECRET in the environment, never here.",
             f"name = {_q(cfg['name'])}", f"bind = {_q(cfg['bind'])}", f"port = {int(cfg['port'])}", ""]
    for k in sorted(cfg["points"]):
        v = cfg["points"][k]
        lines += [f"[points.{_q(k)}]", f"url = {_q(v['url'])}", f"note = {_q(v.get('note', ''))}", ""]
    p.write_text("\n".join(lines))
    record("config saved", env, peers=sorted(cfg["points"]))
    return p


def doctor(cfg: dict, env=os.environ, pinger=None) -> list[tuple]:
    """(ok, what, how): True ✅, False ❌, None ⚠️. The same rows in the CLI, the TUI and the page."""
    rows = []
    try:
        key = secret(env)
        rows.append((True, f"{ENV} set ({len(key)} characters)", ""))
    except ValueError as e:
        return [(False, f"{ENV} not set or too short", str(e))]
    local = cfg["bind"] in ("127.0.0.1", "::1", "localhost")
    rows.append((True if local else None, f"serve listens on {cfg['bind']}:{cfg['port']}",
                 "" if local else "only on a LAN you own, a VPN or a tunnel: every machine that reaches it can try the secret"))
    if not cfg["points"]:
        rows.append((None, "no peers yet", "add one: buddy tui → Peers, or the workbench's Setup tab"))
    pinger = pinger or ping
    for k, v in sorted(cfg["points"].items()):
        r = pinger(v["url"], cfg["name"], key)
        record("check", env, peer=k, ok=r["ok"], why=r.get("why", ""), skew=r.get("skew"), ms=r.get("ms"))
        if r["ok"]:
            rows.append((True, f"{k} {v['url']}: same secret, clocks {abs(r['skew'])} s apart, {r['ms']} ms", ""))
        else:
            rows.append((False, f"{k} {v['url']}: {r['why']}", "is `buddy peer serve` running there, with the same secret?"))
    return rows
