"""`buddy scan feishu|lark`: add (or replace) a Feishu or Lark app on a buddy that is already set up,
by scanning a QR code, and be paired and linked in the same step (the owner, 2026-11-12: "although
init supports scanning, I advise a more standalone way to quickly scan and run").

    buddy scan feishu [--new | --current | --app ID] [--yes] [--restart]

First a choice, as the official lark-cli's `config init` asks it: a new app (the QR code below),
the app this buddy uses now, or another one you have (its App ID and Secret). For an app that
exists, the scan checks its scopes and shows a QR code of the scan-to-enable link
(feishu_scan.enable_url): the developer console opens on that app with only what the buddy
needs to turn on; Enter checks again. A new app:

1. The QR code (feishu_scan.py: the SDK's app registration); the owner scans it and confirms.
   A Feishu scan on a Lark tenant makes a Lark app: it goes where it belongs.
2. Its App ID and Secret into .env, through the Config view's own model (settings.change and
   write: backups, and the Lark ids move to lark:... when Feishu becomes the first app).
3. The new app writes to the owner (install.hello): that pairs the owner's id and links the chat
   it made, in the form this app's ids take (lark:... for the second app).
4. Doctor's rows for that app, then the restart that brings it live: asked, or --restart.

Replacing an app that is there already is asked first (--yes to skip). Nothing is written when
the scan is cancelled or fails.
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Callable
from pathlib import Path

from . import configure, install, settings

BRANDS = ("feishu", "lark")


def apply(s: dict, got: dict) -> tuple[str, str, str]:
    """The scanned app into the settings s (in place): (where it went, the paired id, the id form's
    prefix). where is "feishu" or "lark"."""
    where = got["domain"]
    why = settings.change(s, where, [got["app_id"], got["secret"]])
    if why:
        raise ValueError(why)
    prefix = "lark:" if where == "lark" and s["feishu"][0] else ""
    me = prefix + got["open_id"] if got.get("open_id") else ""
    if me and me not in s["allowed"]:
        s["allowed"].append(me)
    return where, me, prefix


def run(path: Path, brand: str, *, yes: bool = False, restart: bool | None = None,
        out: Callable[[str], None] = print, ask: Callable[[str], str] | None = None,
        scanner=None, greeter=None, use: str | None = None, app_id: str = "", secret: str = "",
        checker=None, ask_secret: Callable[[str], str] | None = None) -> int:
    """The whole flow; 0 when the app was written or checked. ask(prompt) -> answer, None when
    not a terminal. use: "new", "current" or "app" (app_id, secret); None asks."""
    if brand not in BRANDS:
        out(f"buddy scan: feishu or lark, not {brand!r}")
        return 2
    if not path.exists():
        out(f"no buddy here yet ({path}): `buddy init --{brand}-scan` sets one up from scratch")
        return 2
    ask = ask or ((lambda p: install._ask(p)) if sys.stdin.isatty() else None)
    s = configure.load(path)
    before = copy.deepcopy(s)
    if use is None:
        cur = s[brand][0]
        if ask is None:
            use = "new"
        else:
            out(f"{brand.capitalize()}: 1) a new app (QR code)" + (f" · 2) the one this buddy uses ({cur})" if cur else "")
                + " · 3) another app you have (its App ID and Secret)")
            pick = (ask("which one? 1, 2 or 3") or "1").strip()
            use = {"2": "current" if cur else "new", "3": "app"}.get(pick, "new")
    if use != "new":
        return existing(path, brand, s, before, use=use, app_id=app_id, secret=secret, yes=yes, restart=restart,
                        out=out, ask=ask, checker=checker, ask_secret=ask_secret)
    if s[brand][0] and not yes:
        if ask is None or (ask(f"a {brand.capitalize()} app is set already ({s[brand][0]}): replace it? y/N") or "n").lower() != "y":
            out("nothing changed")
            return 1
    try:
        got = (scanner or install.scan_app)(brand)
    except (RuntimeError, KeyboardInterrupt) as e:
        out(f"no app made: {e or 'cancelled'}")
        return 2
    try:
        where, me, prefix = apply(s, got)
    except ValueError as e:
        out(f"not written: {e}")
        return 2
    out(f"{where.capitalize()} app {got['app_id']} made" + (f" (the scan found a {where} tenant)" if where != brand else "")
        + (f"; you are {me}" if me else ""))
    chat = (greeter or install.hello)(got["app_id"], got["secret"], where, got["open_id"]) if got.get("open_id") else ""
    if chat and prefix + chat not in s["chats"]:
        s["chats"].append(prefix + chat)
    ok, notes = settings.write(path, s, before)
    for n in notes:
        if n != settings.RESTART:
            out(n)
    if not ok:
        return 2
    out(f"your chat {prefix + chat} is linked: the bot wrote to you there" if chat else
        "your chat isn't linked yet: write anything to the bot after the restart, then Link in the "
        "Setup tab (or buddy tui → Config, L)")
    out("note: a QR scan makes a PersonalAgent app; if its chats refuse thread replies, topics and long "
        "stories answer in the main chat, and buddy doctor says so")
    settings.reload_env(path)
    label = "Lark" if where == "lark" else "Feishu"
    rows = [r for r in settings.doctor(path, offline=False, peers=False)
            if r[1].startswith((label, f"{label} app", where)) or got["app_id"] in r[1]]
    for ok_, what, how in rows:
        out(f"{'✅' if ok_ else '⚠️ ' if ok_ is None else '❌'} {what}"
            + (f"\n     {how}" if how else ""))
    if restart is None:
        restart = ask is not None and (ask("restart the buddy now, so it listens on the new app? Y/n") or "y").lower() != "n"
    out(settings.restart_soon() if restart else f"restart the buddy to use it: {install.restart_hint('buddy')}")
    return 0


def missing_scopes(got: dict) -> list[str]:
    """The scopes the buddy needs (install.SCOPES, needed=True) this app has not been granted."""
    return [sc for sc, _, need in install.SCOPES if need is True and not got["scopes"].get(sc)]


def existing(path: Path, brand: str, s: dict, before: dict, *, use: str, app_id: str, secret: str, yes: bool,
             restart: bool | None, out, ask, checker=None, ask_secret=None) -> int:
    """An app that exists: its credentials checked, written if they are new here, its scopes
    checked, and the scan-to-enable QR code for what is missing; Enter checks again."""
    from . import feishu_scan as F
    check = checker or install._lark
    if use == "current":
        app_id, secret = s[brand]
        if not app_id:
            out(f"no {brand.capitalize()} app set yet: buddy scan {brand} --new, or --app ID")
            return 2
    else:
        app_id = app_id or (ask("App ID") if ask else "")
        if not secret and ask is not None:
            secret = (ask_secret or (lambda q: __import__("getpass").getpass(q + ": ")))("App Secret (hidden)")
        if not (app_id and secret):
            out("an App ID and its Secret: --app ID, and the Secret when asked")
            return 2
    got = check(app_id.strip(), secret.strip(), brand)
    if "error" in got:
        out(f"{app_id}: {got['error']} (check the App ID and Secret: {install.CONSOLE[brand].format(id=app_id)})")
        return 2
    changed = [app_id, secret] != s[brand]
    if changed:
        if s[brand][0] and not yes and (ask is None or (ask(f"replace the {brand.capitalize()} app {s[brand][0]} "
                                                              f"with {app_id}? y/N") or "n").lower() != "y"):
            out("nothing changed")
            return 1
        try:
            why = settings.change(s, brand, [app_id, secret])
            if why:
                raise ValueError(why)
        except ValueError as e:
            out(f"not written: {e}")
            return 2
        ok, notes = settings.write(path, s, before)
        for n in notes:
            if n != settings.RESTART:
                out(n)
        if not ok:
            return 2
        out(f"{app_id} written. To pair: after the restart write anything to the bot, then Pair in the Setup "
            "tab (P in buddy tui)")
    while True:
        miss = missing_scopes(got)
        out(f"{app_id}: " + ("✅ every scope the buddy needs is granted" if not miss
                             else f"❌ not granted yet: {', '.join(miss)}"))
        url = F.enable_url(brand, app_id, miss, F.EVENTS, F.CALLBACKS)
        drawn = F.qr_text(url)
        out((drawn + "\n" if drawn else "") + "Scan it (or open the link): the developer console opens on this app "
            "with what the buddy needs, " + ("the scopes above, " if miss else "")
            + "the message event and the card callback (those two can't be read back from here: turning on what "
              "is on already changes nothing). Turn them on; publish a version if it asks.\n" + url)
        if ask is None or (ask("Enter: check again · q: done") or "").strip().lower() == "q":
            break
        got = check(app_id, secret, brand)
        if "error" in got:
            out(f"{app_id}: {got['error']}")
            break
    if changed:
        if restart is None:
            restart = ask is not None and (ask("restart the buddy now, so it uses this app? Y/n") or "y").lower() != "n"
        out(settings.restart_soon() if restart else f"restart the buddy to use it: {install.restart_hint('buddy')}")
    return 0
