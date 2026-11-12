"""A Feishu/Lark send-and-press test that runs beside the live buddy, never inside it.

    BRAHMAN_TEST_APP_ID=cli_... BRAHMAN_TEST_APP_SECRET=... \\
        uv run python -m work_brahman.presstest [--domain feishu|lark] [--timeout 300]

It opens its own long connection for a test app (its own ids, never the buddy's FEISHU_APP_ID
or LARK_APP_ID), so a second `buddy serve` is never started and the live app's events are
never split. Then:

  1. waits for any message to the bot ("hi" from the app's chat): that is the chat to use, and
     proves the event (im.message.receive_v1) arrives over the long connection;
  2. posts a card with one button there (im:message:send_as_bot);
  3. waits for the press to come back (the card.action.trigger callback);
  4. prints each step with ✅ or ❌ and the time it took; exit 0 only when all three passed.

While it runs it is also the connected client the console needs to save "receive events (and
callbacks) through long connection".
"""

from __future__ import annotations

import argparse
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

from .channel import Incoming

ACTION = "ask:presstest:press"


def run(channel, inbox: "queue.Queue[Incoming]", timeout: float, say=print, broken=lambda: "") -> int:
    """The test against a started channel whose messages land in inbox. Returns the exit code.
    broken: why the connection died ("" while it lives), so a wrong secret or domain is said at
    once instead of a 300 s wait that blames the console."""
    t0 = time.monotonic()
    say(f"… waiting up to {timeout:.0f} s for a message to the bot (send it \"hi\")")
    first = _next(inbox, lambda m: not m.action, t0 + timeout, broken)
    if broken():
        say(f"❌ could not connect: check the App ID / Secret and --domain ({broken()})")
        return 1
    if first is None:
        say("❌ no message arrived: is im.message.receive_v1 subscribed through long connection, "
            "the version published, and you in its availability scope?")
        return 1
    say(f"✅ message received in {time.monotonic() - t0:.1f} s: chat {first.chat}, sender {first.sender}")
    t1 = time.monotonic()
    handle = channel.ask(first.chat, "Press the button: its press should come back to this test.",
                         title="brahman press test", options=[["press", "Press me / 按我"]],
                         ask_id="presstest")
    if not handle:
        say("❌ the card was not posted: is im:message:send_as_bot granted (and the version published)?")
        return 1
    say(f"✅ card posted in {time.monotonic() - t1:.1f} s (message {handle}); now press its button")
    t2 = time.monotonic()
    press = _next(inbox, lambda m: m.action == ACTION, t2 + timeout, broken)
    if broken():
        say(f"❌ the connection died: {broken()}")
        return 1
    if press is None:
        say("❌ no press came back: is the card.action.trigger callback subscribed through long connection?")
        return 1
    say(f"✅ press came back in {time.monotonic() - t2:.1f} s from {press.sender}: post, press, back all work")
    return 0


def _next(inbox, want, deadline: float, broken=lambda: "") -> Incoming | None:
    while (left := deadline - time.monotonic()) > 0 and not broken():
        try:
            m = inbox.get(timeout=min(left, 1.0))
        except queue.Empty:
            continue
        if want(m):
            return m
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m work_brahman.presstest", description=__doc__.split("\n\n")[0])
    p.add_argument("--domain", choices=["feishu", "lark"], default="feishu")
    p.add_argument("--timeout", type=float, default=300, help="seconds to wait at each step")
    a = p.parse_args(argv)
    app_id, secret = os.environ.get("BRAHMAN_TEST_APP_ID", ""), os.environ.get("BRAHMAN_TEST_APP_SECRET", "")
    if not app_id or not secret:
        print("set BRAHMAN_TEST_APP_ID and BRAHMAN_TEST_APP_SECRET (the test app's, not the live buddy's)",
              file=sys.stderr)
        return 2
    if app_id in live_app_ids():
        print("that is the live buddy's app: its events would be split between two connections", file=sys.stderr)
        return 2
    from .feishu import Feishu
    ch = Feishu(app_id, secret, domain=a.domain)
    inbox: "queue.Queue[Incoming]" = queue.Queue()
    died: list[str] = []

    def listen():
        try:
            ch.listen(inbox.put)
        except BaseException as e:  # a wrong secret or domain: say so, don't wait it out
            died.append(f"{type(e).__name__}: {e}"[:300])
    threading.Thread(target=listen, name="presstest-ws", daemon=True).start()
    print(f"connecting as {app_id} on {a.domain}")
    return run(ch, inbox, a.timeout, broken=lambda: died[0] if died else "")


def live_app_ids() -> set[str]:
    """The Lark/Feishu App IDs a buddy on this machine uses: this environment's, and those in the
    .env files it reads, the config folder's, a checkout's beside buddy.toml, and the service's
    EnvironmentFile (systemd). The live ids are rarely in a shell's environment: the service reads
    them from its .env (2026-11-10 review: the guard never fired from a terminal)."""
    from . import install
    files = {install.CONFIG_DIR / ".env", Path("buddy.toml").resolve().parent / ".env"}
    try:
        unit = install.service_file("buddy").read_text()
        files |= {Path(m.strip().lstrip("-").strip("'\"")) for m in re.findall(r"^EnvironmentFile=(.+)$", unit, re.M)}
    except OSError:
        pass
    ids = {os.environ.get(k, "") for k in ("FEISHU_APP_ID", "LARK_APP_ID")}
    for f in files:
        try:
            for line in f.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() in ("FEISHU_APP_ID", "LARK_APP_ID"):
                    ids.add(v.strip().strip("'\""))
        except OSError:
            continue
    return ids - {""}


if __name__ == "__main__":
    sys.exit(main())
