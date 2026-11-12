"""Make the Feishu/Lark app by scanning a QR code, instead of clicking through the developer
console (optional: `buddy init` still takes an App ID and Secret typed in; the owner,
2026-11-12: "build feishu part first, but it's optional").

Underneath it is the Lark SDK's app registration (lark_oapi.scene.registration.register_app),
an OAuth 2.0 Device Authorization Grant (RFC 8628) against accounts.feishu.cn
/oauth/v1/app/registration: `begin` gives a device code and a link; the owner opens the link (or
scans it as a QR code) in Feishu/Lark and confirms; the SDK polls until the App ID and Secret
come back, with the owner's open_id. A Lark tenant switches to accounts.larksuite.com by itself
(tenant_brand), so one scan serves both. `addons` asks for what the buddy needs (install.SCOPES,
the message event, the card callback) at creation; whether the platform grants them at once or
only requests them, and whether events come set to the long connection, a real run tells:
`buddy doctor` checks the scopes afterwards.

The QR code is drawn in the terminal when `segno` is installed (pure Python, optional:
`uv pip install segno`); without it the link is printed, to open or to turn into a QR code.
"""

from __future__ import annotations

from collections.abc import Callable

NAME = "brahman buddy"
DESC = "A personal buddy: chats, short and long stories (work_brahman)"
EVENTS = ["im.message.receive_v1"]  # messages reach the buddy (feishu.py listen)
CALLBACKS = ["card.action.trigger"]  # button presses: asks, ■ Stop
ACCOUNTS = {"feishu": "https://accounts.feishu.cn", "lark": "https://accounts.larksuite.com"}


def addons(scopes: list[str]) -> dict:
    return {"scopes": {"tenant": list(scopes)}, "events": {"items": {"tenant": list(EVENTS)}},
            "callbacks": {"items": list(CALLBACKS)}}


OPEN = {"feishu": "https://open.feishu.cn", "lark": "https://open.larksuite.com"}


def encode_addons(addons: dict) -> str:
    """JSON -> gzip -> base64url without padding: the launcher page's form (as lark_oapi's
    registration and the official lark-cli encode it)."""
    import base64
    import gzip
    import json
    raw = json.dumps(addons, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(gzip.compress(raw, mtime=0)).decode().rstrip("=")


def enable_url(domain: str, app_id: str, scopes: list[str] = (), events: list[str] = (),
               callbacks: list[str] = ()) -> str:
    """The scan-to-enable link for an app that exists already (the owner, 2026-11-12: "choose a
    ready-to-use bot and check its permissions, as the Feishu CLI supports"): the developer
    console opens on that app with only these scopes, events and callbacks to turn on. The
    contract is the official lark-cli's (cmd/event/console_url.go): {open}/page/launcher?
    clientID=<app>&addons=<encoded>, clientID in camelCase, tenant and user always arrays."""
    import urllib.parse
    addons = {}
    if scopes:
        addons["scopes"] = {"tenant": list(scopes), "user": []}
    if events:
        addons["events"] = {"items": {"tenant": list(events), "user": []}}
    if callbacks:
        addons["callbacks"] = {"items": list(callbacks)}
    q = {"clientID": app_id} | ({"addons": encode_addons(addons)} if addons else {})
    return f"{OPEN[domain]}/page/launcher?{urllib.parse.urlencode(q)}"


def qr_text(url: str) -> str:
    """The link as a QR code for a terminal, or "" without segno."""
    try:
        import segno
    except ImportError:
        return ""
    import io
    out = io.StringIO()
    try:
        segno.make(url, error="l").terminal(out=out, compact=True)
    except Exception:  # an older segno: the link alone still works
        return ""
    return out.getvalue()


def scan(brand: str, scopes: list[str], out: Callable[[str], None] = print,
         register=None) -> dict:
    """Show the QR code, wait for the owner's scan, return {app_id, secret, domain, open_id}.
    brand: "feishu" or "lark", where to start (a Lark tenant is switched to by itself). Raises
    RuntimeError with what to do when it is refused, expires or fails."""
    if register is None:
        from lark_oapi.scene.registration import register_app as register
    from lark_oapi.scene.registration.errors import AppAccessDeniedError, AppExpiredError, RegisterAppError

    def on_qr(info: dict) -> None:
        minutes = max(1, int(info.get("expire_in", 600)) // 60)
        drawn = qr_text(info["url"])
        out((drawn + "\n" if drawn else "") + f"Scan it with {brand.capitalize()} (or open the link and "
            f"confirm), within {minutes} minutes:\n  {info['url']}")
        if not drawn:
            out("  (no QR code drawn: `uv pip install segno` draws one here; or open the link on this machine)")

    def on_status(info: dict) -> None:
        if info.get("status") == "domain_switched":
            out("a Lark tenant: going on with Lark (open.larksuite.com)")

    try:
        got = register(on_qr, on_status_change=on_status, source="brahman-buddy",
                       domain=ACCOUNTS["lark" if brand == "lark" else "feishu"], lark_domain=ACCOUNTS["lark"],
                       app_preset={"name": NAME, "desc": DESC}, addons=addons(scopes))
    except AppAccessDeniedError as e:
        raise RuntimeError(f"refused in {brand.capitalize()} ({e}); type the App ID and Secret instead") from e
    except AppExpiredError as e:
        raise RuntimeError("the QR code expired before it was confirmed; run it again") from e
    except RegisterAppError as e:
        raise RuntimeError(f"the app could not be made this way ({e}); type the App ID and Secret instead") from e
    user = got.get("user_info") or {}
    return {"app_id": got["client_id"], "secret": got["client_secret"],
            "domain": "lark" if user.get("tenant_brand") == "lark" else ("lark" if brand == "lark" else "feishu"),
            "open_id": user.get("open_id", "")}
