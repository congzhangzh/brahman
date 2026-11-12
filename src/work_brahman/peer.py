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

**Who may connect** (the owner, 2026-11-12: "default only allow from 127.0.0.1 … and a global
switch to allow public remote join"): `allow` in peers.toml, checked on every connection, not
only by the bind. "local" (the default): 127.0.0.1 and ::1 only; an SSH tunnel's far end arrives
as 127.0.0.1, because the local ssh/sshd opens the connection. That means "some process on this
machine", not "trusted": the secret stays the gate. "private": also LAN and VPN ranges (10/8,
172.16/12, 192.168/16, Tailscale's 100.64/10, fc00::/7, link-local). "public": anyone, plain
HTTP across the internet: doctor says so in red. The bind follows `allow` unless one is set.

**Resources** (the owner: "a resource table, which defines each item and how it maps for each
participant, like a git server on a different port by ssh … real P2P and privacy"): a shared
name, a local address. [resources.<name>] says how THIS machine reaches it (kind, url); each
machine keeps its own, never sent. Steps and notes name a resource; the signed ping tells a
peer only which names this side has. Doctor checks each from here.

**This side's description** (the owner: "peer should also have description"): what this
machine is and is good for, written by its owner, sent in the ping answer so the peers learn
it (peers-seen.json keeps the last one heard). Seen by peers: no secrets in it. A peer's `note`
is the other thing: your own word about it, never sent.
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
ALLOW = ("local", "private", "public")
KINDS = ("git", "http", "ssh", "tcp", "other")
MAX_DESC = 300
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


# Events: one story on every machine (specs/multi-brahman.md, "One story on every machine", the
# owner, 2026-11-12). A step's owner sends what changed; every peer keeps it. Step 1 of the build
# order: events are checked and kept, per story, never applied here (the story copy is step 2).
EVENT_KINDS = ("plan", "start", "mark", "note", "done", "fail", "answer")
EVENT_FIELDS = {"story": str, "step": str, "origin": str, "seq": int, "what": str, "text": str,
                "at": str, "by": str, "bundle": str}
STORY_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}#[0-9]{1,9}$")
MAX_EVENT = 16 * 1024


def check_event(event, sender: str) -> str:
    """"" when the event is well formed and its origin is who signed it, else why not. Strict:
    only these fields and kinds, short text; nothing in it is ever run."""
    if not isinstance(event, dict):
        return "an event is a JSON object"
    extra = set(event) - set(EVENT_FIELDS)
    if extra:
        return f"unknown fields: {', '.join(sorted(extra))}"
    for k in ("story", "step", "origin", "seq", "what", "at"):
        if k not in event:
            return f"missing {k}"
    for k, kind in EVENT_FIELDS.items():
        if k in event and not isinstance(event[k], kind) or (kind is int and isinstance(event.get(k), bool)):
            return f"{k} must be {kind.__name__}"
    if not STORY_RE.match(event["story"]):
        return "story is <machine>#<number>, e.g. a#12"
    if event["origin"] != sender:
        return "origin is not the sender who signed it"
    if event["what"] not in EVENT_KINDS:
        return f"what is one of {', '.join(EVENT_KINDS)}"
    if event["seq"] < 1 or len(event["step"]) > 80 or len(event.get("text", "")) > 4000 \
            or len(event.get("by", "")) > 80 or len(event.get("bundle", "")) > 80 or len(event["at"]) > 40:
        return "seq from 1; step, by, bundle up to 80 characters, text up to 4000"
    return ""


def events_dir(env=os.environ) -> Path:
    return Path(env.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman" / "peer-events"


def keep_event(event: dict, env=os.environ) -> bool:
    """Append it to its story's log; False when that (origin, seq) is there already (a replay
    after being away sends it again: kept once)."""
    d = events_dir(env)
    d.mkdir(parents=True, exist_ok=True)
    f = d / (event["story"].replace("#", "-") + ".jsonl")
    if f.exists():
        for line in f.read_text().splitlines():
            e = json.loads(line)
            if (e["origin"], e["seq"]) == (event["origin"], event["seq"]):
                return False
    with f.open("a") as out:
        out.write(json.dumps(event, ensure_ascii=False) + "\n")
    return True


def events(story: str = "", env=os.environ) -> list[dict]:
    """The kept events, of one story or all, in the order they came."""
    d = events_dir(env)
    files = [d / (story.replace("#", "-") + ".jsonl")] if story else sorted(d.glob("*.jsonl")) if d.exists() else []
    return [json.loads(x) for f in files if f.exists() for x in f.read_text().splitlines() if x.strip()]


def send_event(url: str, sender: str, event: dict, key: bytes, now: float | None = None) -> str:
    body = json.dumps(event, ensure_ascii=False).encode()
    when = int(now if now is not None else time.time())
    req = urllib.request.Request(url.rstrip("/") + "/event", data=body, method="POST", headers={
        "X-Peer-From": sender, "X-Peer-Time": str(when), "X-Peer-Sig": sign(key, sender, when, body),
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode().strip()
    except urllib.error.HTTPError as e:
        raise ValueError(f"refused: {e.read().decode().strip()}") from e


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


def allowed(ip: str, allow: str) -> bool:
    """May a connection from `ip` be served under `allow` (local / private / public)?"""
    import ipaddress
    if allow == "public":
        return True
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    if a.is_loopback:
        return True
    if allow != "private":
        return False
    return a.is_private or a.is_link_local or (a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10"))


def listen(cfg: dict) -> str:
    """Where serve binds: the bind set in peers.toml, else 127.0.0.1 for allow = local, else all."""
    return cfg.get("bind") or ("127.0.0.1" if cfg.get("allow", "local") == "local" else "0.0.0.0")


def server(bind: str, port: int, inbox: Path, key: bytes, log=print, name: str = "",
           env=os.environ, allow: str = "local", description: str = "",
           resources: list[str] | None = None) -> ThreadingHTTPServer:
    seen: dict = {}
    me = name or default_name()
    fp = fingerprint()
    res = sorted(resources or [])

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

        def _refused_ip(self) -> bool:
            """Not from where `allow` lets in: answered 403 before anything is read or checked."""
            ip = self.client_address[0]
            if allowed(ip, allow):
                return False
            record("refused", env, peer=self.headers.get("X-Peer-From", "?"), ip=ip, why=f"allow = {allow}")
            self._answer(403, f"not from here: this side allows {allow} connections only")
            return True

        def do_GET(self):
            """GET /events[?story=a#12]: everything kept here, for a peer catching up (a pull, like
            DICOM's C-GET: the side that can reach asks). Signed over an empty body, as POSTs."""
            if self._refused_ip():
                return
            path, _, query = self.path.partition("?")
            if path != "/events":
                return self._answer(404, "only GET /events")
            sender = self.headers.get("X-Peer-From", "?")
            why = verify(key, self.headers, b"", time.time(), seen)
            if why:
                record("pull refused", env, peer=sender, ip=self.client_address[0], why=why)
                return self._answer(403, why)
            story = urllib.parse.parse_qs(query).get("story", [""])[0]
            data = json.dumps(events(story, env), ensure_ascii=False).encode()
            record("pulled", env, peer=sender, story=story, bytes=len(data))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self._refused_ip():
                return
            if self.path == "/ping":  # a check: answered signed, so the caller knows we hold the secret too
                body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 64))
                why = verify(key, self.headers, body, time.time(), seen)
                sender = self.headers.get("X-Peer-From", "?")
                record("ping refused" if why else "ping", env, peer=sender, ip=self.client_address[0], why=why)
                if why:
                    return self._answer(403, why)
                now = int(time.time())
                data = json.dumps({"name": me, "time": now, "fp": fp, "desc": description, "res": res},
                                  ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Peer-Sig", sign(key, me, now, self.headers.get("X-Peer-Sig", "").encode() + data))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if self.path == "/event":  # a story event (specs/multi-brahman.md): checked, kept, never run
                size = int(self.headers.get("Content-Length") or 0)
                if size <= 0 or size > MAX_EVENT:
                    return self._answer(413, f"an event of 1 byte to {MAX_EVENT // 1024} KB")
                body = self.rfile.read(size)
                sender = self.headers.get("X-Peer-From", "?")
                why = verify(key, self.headers, body, time.time(), seen)
                event = None
                if not why:
                    try:
                        event = json.loads(body)
                    except ValueError:
                        why = "not JSON"
                    else:
                        why = check_event(event, sender)
                if why:
                    record("event refused", env, peer=sender, ip=self.client_address[0], why=why)
                    return self._answer(403, why)
                new = keep_event(event, env)
                if new:  # on the machine the story started on: its card moves (peer_story.apply)
                    from . import peer_story
                    peer_story.apply(event, me, env)
                record("event", env, peer=sender, story=event["story"], step=event["step"], what=event["what"],
                       seq=event["seq"], new=new)
                return self._answer(200, f"kept {event['story']} {event['origin']}:{event['seq']}" if new
                                    else f"had {event['story']} {event['origin']}:{event['seq']} already")
            if self.path != "/bundle":
                return self._answer(404, "only POST /bundle, /event or /ping")
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
    if args.action == "story" and args.story_action in ("show", "list"):
        from . import peer_story as S
        if args.story_action == "list":
            for sid in S.stories():
                v = S.view(sid)
                done = sum(s["status"] == "done" for s in v["steps"]) if v else 0
                print(f"{sid:<16} {done}/{len(v['steps']) if v else '?'} done  {v['title'] if v else '(no plan yet)'}")
            return 0
        v = S.view(args.story)
        if v is None:
            print(f"no plan for {args.story} here: buddy peer story sync", file=sys.stderr)
            return 1
        print(S.show(v))
        return 0
    if args.action == "events":
        rows = events(args.story)
        for e in rows:
            print(f"{e['at'][:19]}  {e['story']:<10} {e['origin']}:{e['seq']:<4} {e['step']:<14} {e['what']:<7} "
                  f"{e.get('by', '')} {e.get('text', '')[:60]}".rstrip())
        if not rows:
            print(f"no events{' for ' + args.story if args.story else ''} in {events_dir()}")
        return 0
    if args.action == "setup":  # the peers block in the global instructions (peer_block.py)
        from . import peer_block
        if args.dry_run:
            print(peer_block.render({**cfg, "instructions": True}, seen()) if args.instructions else "would take the block out")
            print("dry run: nothing changed")
            return 0
        save({**cfg, "instructions": bool(args.instructions)})
        for x in peer_block.state():
            print(f"{'✅ in' if x['has'] else '–  not in'} {x['file']}")
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
            bind, port = args.bind or listen(cfg), args.port or cfg["port"]
            if bind not in ("127.0.0.1", "::1", "localhost"):
                print(f"⚠️  listening on {bind}: every machine that can reach it can try the secret", file=sys.stderr)
            if cfg["allow"] == "public":
                print("⚠️  allow = public: anyone on the internet may connect, and what is sent crosses in plain "
                      "text (the secret signs it, it does not hide it)", file=sys.stderr)
            srv = server(bind, port, inbox_dir(), key, log=lambda s: print(s, flush=True), name=cfg["name"],
                         allow=cfg["allow"], description=cfg["description"], resources=list(cfg["resources"]))
            print(f"receiving on http://{bind}:{port}/bundle into {inbox_dir()} (allow = {cfg['allow']})", flush=True)
            record("serve", bind=bind, port=port, allow=cfg["allow"])
            srv.serve_forever()
            return 0
        sender = getattr(args, "name", "") or cfg["name"]
        if not NAME_RE.match(sender):
            raise ValueError("--name: letters, digits, . _ - only, at most 40")
        if args.action == "story":
            from . import peer_story as S
            if args.story_action == "start":
                sid, said = S.start(args.title, args.step, cfg, key, config=Path(args.config) if getattr(args, "config", None) else None)
                print(f"started {sid}")
            elif args.story_action == "mark":
                said = S.mark(args.story, args.step, args.status, cfg, key, note=args.note, by=args.by)
            else:
                said = S.sync(cfg, key, peers=args.peers or None)
            for k, v in said.items():
                print(f"  {k}: {v}")
            return 0
        point = cfg["points"].get(args.url)
        url = point["url"] if point else args.url
        if args.action == "event":  # one story event to one peer; kept here too (this machine's copy)
            mine = [e["seq"] for e in events(args.story) if e["origin"] == sender]
            event = {"story": args.story, "step": args.step, "origin": sender, "seq": max(mine, default=0) + 1,
                     "what": args.what, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            event.update({k: v for k, v in (("text", args.text), ("by", args.by), ("bundle", args.bundle)) if v})
            why = check_event(event, sender)
            if why:
                raise ValueError(why)
            said = send_event(url, sender, event, key)
            keep_event(event)
            record("event sent", peer=args.url, story=args.story, step=args.step, what=args.what, seq=event["seq"])
            print(said)
            return 0
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
    res = info.get("res") if isinstance(info.get("res"), list) else []
    return {"ok": not why, "why": why, "name": name, "skew": skew, "ms": ms, "fp": str(info.get("fp", "")),
            "desc": clean_desc(str(info.get("desc") or "")),
            "res": sorted({str(x) for x in res if isinstance(x, str) and NAME_RE.match(x)})[:50]}


def clean_desc(text: str) -> str:
    """A description as kept and shown: one line, no control characters, at most MAX_DESC."""
    return " ".join("".join(c for c in text if c.isprintable() or c in "\t\n").split())[:MAX_DESC]


def seen_path(env=os.environ) -> Path:
    return log_path(env).with_name("peers-seen.json")


def seen(env=os.environ) -> dict:
    """What each peer last said of itself in a ping answer: {peer: {name, desc, res, fp, at}}."""
    try:
        d = json.loads(seen_path(env).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(peer: str, r: dict, env=os.environ) -> None:
    """Keep what a peer said of itself (a good ping only)."""
    if not r.get("ok") and not r.get("name"):
        return
    d = seen(env)
    d[peer] = {"name": r.get("name", ""), "desc": r.get("desc", ""), "res": r.get("res", []), "fp": r.get("fp", ""),
               "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    p = seen_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1))


def check_resource(r: dict, timeout: float = 8.0) -> tuple[bool | None, str]:
    """Can THIS machine reach the resource? (True, how) / (False, why) / (None, "not checked")."""
    kind, url = r.get("kind", "other"), r.get("url", "")
    if kind == "git":
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
               "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=5"}
        try:
            p = subprocess.run(["git", "ls-remote", "--heads", url], capture_output=True, text=True,
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return False, f"git ls-remote: no answer in {int(timeout)} s"
        except OSError as e:
            return False, f"git: {e}"
        if p.returncode == 0:
            return True, f"git answers ({len(p.stdout.splitlines())} branches)"
        return False, (p.stderr.strip().splitlines() or ["git ls-remote failed"])[-1][:200]
    if kind == "http":
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=timeout) as resp:
                return True, f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            return (True, f"HTTP {e.code}") if e.code < 500 else (False, f"HTTP {e.code}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            return False, f"unreachable: {getattr(e, 'reason', e)}"
    if kind in ("ssh", "tcp"):
        u = urllib.parse.urlparse(url if "://" in url else f"{kind}://{url}")
        port = u.port or (22 if kind == "ssh" else 0)
        if not u.hostname or not port:
            return False, "the url needs host:port"
        try:
            with socket.create_connection((u.hostname, port), timeout=timeout):
                return True, f"{u.hostname}:{port} accepts"
        except OSError as e:
            return False, f"{u.hostname}:{port}: {e.strerror or e}"
    return None, "kind other: not checked"


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
              | ({"direction": "in"} if v.get("direction") == "in" else {})  # send-only: never pushed to
              for k, v in (d.get("points") or {}).items() if isinstance(v, dict)}
    resources = {k: {"kind": str(v.get("kind") or "other"), "url": str(v.get("url", "")), "note": str(v.get("note", ""))}
                 for k, v in (d.get("resources") or {}).items() if isinstance(v, dict)}
    allow = str(d.get("allow") or "local")
    return {"name": str(d.get("name") or default_name()),
            "bind": str(d.get("bind") or ""), "port": int(d.get("port") or 8790), "points": points,
            "allow": allow if allow in ALLOW else "local", "description": clean_desc(str(d.get("description") or "")),
            "resources": resources, "instructions": bool(d.get("instructions", False)),
            "fingerprint": str(d.get("fingerprint", ""))}  # the machine it was written on


def default_name() -> str:
    """This side's name when peers.toml gives none: the short hostname, made a valid name
    (lower case; anything but letters, digits, . _ - becomes -; at most 40)."""
    raw = socket.gethostname().split(".")[0].lower()
    name = re.sub(r"[^a-z0-9._-]", "-", raw).strip("-")[:40]
    return name or "machine"


def fingerprint() -> str:
    """This machine, as a short hash (the owner, 2026-11-12: the name "internally tied to an
    environment fingerprint, like the MAC"): /etc/machine-id on Linux, the platform UUID on
    macOS, MachineGuid on Windows, else the MAC address. Only the hash leaves the machine, so
    the id itself is never sent; stable across reboots and renames."""
    import hashlib as _h
    import uuid
    raw = ""
    for f in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            raw = Path(f).read_text().strip()
            break
        except OSError:
            pass
    if not raw and sys.platform == "darwin":
        try:
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True,
                                 timeout=5).stdout
            m = re.search(r'"IOPlatformUUID" = "([^"]+)"', out)
            raw = m[1] if m else ""
        except (OSError, subprocess.SubprocessError):
            pass
    if not raw and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                raw = winreg.QueryValueEx(k, "MachineGuid")[0]
        except OSError:
            pass
    raw = raw or f"mac:{uuid.getnode():012x}"
    return _h.sha256(f"brahman-peer:{raw}".encode()).hexdigest()[:12]


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a TOML basic string is a JSON string


def validate(cfg: dict) -> str:
    """"" when it may be saved, else what is wrong."""
    if not NAME_RE.match(cfg["name"]):
        return "this side's name: letters, digits, . _ - only, at most 40"
    if not (0 < int(cfg["port"]) < 65536):
        return "the port: 1 to 65535"
    if cfg.get("allow", "local") not in ALLOW:
        return "allow: local, private or public"
    if len(cfg.get("description", "")) > MAX_DESC:
        return f"this side's description: at most {MAX_DESC} characters"
    for k, v in (cfg.get("resources") or {}).items():
        if not NAME_RE.match(k):
            return f"resource {k!r}: letters, digits, . _ - only, at most 40"
        if v.get("kind") not in KINDS:
            return f"resource {k}: the kind is one of {', '.join(KINDS)}"
        if not v.get("url", "").strip():
            return f"resource {k}: how this machine reaches it (a url)"
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
             f"name = {_q(cfg['name'])}",
             f"description = {_q(clean_desc(cfg.get('description', '')))}  # sent to the peers in the ping: no secrets",
             f"allow = {_q(cfg.get('allow', 'local'))}  # who may connect: local (127.0.0.1, tunnels) | private (LAN, VPN) | public"]
    lines += [f"bind = {_q(cfg['bind'])}"] if cfg.get("bind") else []
    lines += [f"port = {int(cfg['port'])}",
              f"instructions = {'true' if cfg.get('instructions') else 'false'}  # this file as a block in ~/.claude/CLAUDE.md (and Codex's AGENTS.md)",
              f"fingerprint = {_q(fingerprint())}  # the machine this was written on (a hash): doctor warns if copied", ""]
    for k in sorted(cfg["points"]):
        v = cfg["points"][k]
        lines += [f"[points.{_q(k)}]", f"url = {_q(v['url'])}", f"note = {_q(v.get('note', ''))}"]
        lines += [f"direction = {_q('in')}  # send-only: it reaches us, we don't reach it"] if v.get("direction") == "in" else []
        lines += [""]
    for k in sorted(cfg.get("resources") or {}):
        v = cfg["resources"][k]
        lines += [f"[resources.{_q(k)}]  # how THIS machine reaches it; the peers only learn the name",
                  f"kind = {_q(v.get('kind', 'other'))}", f"url = {_q(v['url'])}", f"note = {_q(v.get('note', ''))}", ""]
    p.write_text("\n".join(lines))
    record("config saved", env, peers=sorted(cfg["points"]), resources=sorted(cfg.get("resources") or {}))
    from . import peer_block
    peer_block.sync(load(env), env)
    return p


def doctor(cfg: dict, env=os.environ, pinger=None) -> list[tuple]:
    """(ok, what, how): True ✅, False ❌, None ⚠️. The same rows in the CLI, the TUI and the page."""
    rows = []
    try:
        key = secret(env)
        rows.append((True, f"{ENV} set ({len(key)} characters)", ""))
    except ValueError as e:
        return [(False, f"{ENV} not set or too short", str(e))]
    mine = fingerprint()
    rows.append((True, f"this side: {cfg['name']} (machine {mine})", ""))
    if cfg.get("fingerprint") and cfg["fingerprint"] != mine:  # peers.toml copied from another machine
        rows.append((None, f"peers.toml was written on another machine ({cfg['fingerprint']}): "
                           f"the name {cfg['name']!r} may be that machine's",
                     "give this side a name of its own (buddy tui → Peers → s, or the Setup tab), then write"))
    if cfg.get("description"):
        rows.append((True, f"this side's description (sent to the peers): {cfg['description']}", ""))
    else:
        rows.append((None, "this side has no description", "say what this machine is and is good for: buddy tui → "
                     "Peers → s, or the Setup tab (the peers learn it in the ping)"))
    bind, allow = listen(cfg), cfg.get("allow", "local")
    loop = bind in ("127.0.0.1", "::1", "localhost")
    if allow == "public":
        rows.append((False, f"allow = public: anyone may connect to {bind}:{cfg['port']}, and bundles and events cross "
                            "in plain text", "only if you mean it: local plus an SSH tunnel, or private on a VPN, keeps "
                            "both the callers and the contents among your machines"))
    elif allow == "private":
        rows.append((None, f"allow = private: this machine, LAN and VPN addresses may connect ({bind}:{cfg['port']})",
                     "a LAN you own or a VPN only: everyone there can try the secret"))
    else:
        rows.append((True, f"allow = local: only this machine and SSH tunnels may connect ({bind}:{cfg['port']})", ""))
    if allow != "local" and loop:
        rows.append((None, f"allow = {allow}, but serve binds {bind}: nothing from outside reaches it",
                     "clear the bind (buddy tui → Peers → s) so it follows allow"))
    if not cfg["points"]:
        rows.append((None, "no peers yet", "add one: buddy tui → Peers, or the workbench's Setup tab"))
    pinger = pinger or ping
    heard: dict = {}
    for k, v in sorted(cfg["points"].items()):
        if v.get("direction") == "in":  # send-only: we can't reach it; what it sent last says it is alive
            last = next((e for e in reversed(read_log(env, 500)) if e.get("peer") == k
                         and e.get("event") in ("event", "received", "ping", "pulled")), None)
            rows.append((True if last else None, f"{k} (send-only): " + (f"last heard {last['at'][:16].replace('T', ' ')}, "
                                                                          f"{last['event']}" if last else "not heard from yet"),
                         "" if last else "it reaches us: buddy peer doctor there, or a sync from it"))
            continue
        r = pinger(v["url"], cfg["name"], key)
        record("check", env, peer=k, ok=r["ok"], why=r.get("why", ""), skew=r.get("skew"), ms=r.get("ms"))
        if r["ok"]:
            remember(k, r, env)
            heard[k] = r
        if r["ok"] and r.get("name") == cfg["name"] and r.get("fp") and r["fp"] != mine:
            rows.append((False, f"{k} {v['url']}: answers as {r['name']!r}, this side's own name, from another "
                                f"machine ({r['fp']})", "two machines with one name mix up their stories: rename one"))
        elif r["ok"]:
            rows.append((True, f"{k} {v['url']}: same secret, clocks {abs(r['skew'])} s apart, {r['ms']} ms"
                         + (f"; it calls itself {r['name']!r}" if r.get("name") and r["name"] != k else ""), ""))
        else:
            rows.append((False, f"{k} {v['url']}: {r['why']}", "is `buddy peer serve` running there, with the same secret?"))
        if r["ok"] and r.get("desc"):
            rows.append((True, f"{k} says: {r['desc']}", ""))
    known = seen(env)
    for k, rv in sorted((cfg.get("resources") or {}).items()):
        ok, how = check_resource(rv)
        have = [p for p in sorted(cfg["points"]) if k in (heard.get(p) or known.get(p) or {}).get("res", [])]
        rows.append((ok, f"resource {k} ({rv['kind']}): " + (how if ok is not None else "not checked")
                     + (f"; also at {', '.join(have)}" if have else ""),
                     "" if ok is not False else f"this machine can't reach it: {how}"))
    names = set(cfg.get("resources") or {})
    for p_, info in sorted(known.items()):
        if p_ in cfg["points"]:
            missing = sorted(set(info.get("res", [])) - names)
            if missing:
                rows.append((None, f"{p_} has resource(s) this side has no address for: {', '.join(missing)}",
                             "add how this machine reaches them, if it should (buddy tui → Peers → r)"))
    from . import peer_block
    peer_block.sync(cfg, env)
    return rows


def set_allow(cfg: dict, allow: str) -> dict:
    """allow changed: a loopback bind goes back to following it, so the switch does what it says."""
    out = {**cfg, "allow": allow}
    if out.get("bind") in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "::"):
        out["bind"] = ""
    return out
