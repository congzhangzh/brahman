# What's new

The buddy shows this in the workbench's **What's new** tab, and posts the top section to your chats
once, at the first start of a new version (following `[notify]`).

## 20261113.2

- **The QR code is drawn** in the terminal by `buddy init` and `buddy scan`: `segno` is a dependency
  now (pure Python, nothing else pulled in), so no extra install is needed.

## 20261113.1

- **What's new**: this tab, and a post of the newest section to your chats at the first start of a
  new version (following `[notify]`, so with the defaults only this tab shows it).
- **Long stories**: in a story's thread, `/topys` brings up the pys window that holds the session or
  takes it over into a new one, `/frompys` gives it back to the buddy, and `/status` says who holds
  it; one name per move (the earlier `/shift`, `/shiftto`, `/shiftfrom`, `/takeover`, `/takeback` and
  `/handover` are gone: `/topys` hands over by itself). The workbench's
  long-story card has the same two buttons, **To pys** and **From pys**.

- **A long story from the web**: `/newlong` typed in the web chat now asks for the task there too;
  the question gets ↩ Reply, and the reply box answers the newest open question by itself, as
  Telegram does.
- **Every story's thread starts with your first question**, in every chat.
- **What's new** shows the running version and its commit, and the page draws Markdown headings
  and quotes.

## 20261112.7

- **Nothing on its own by default**: `[notify]` now covers every app (Telegram, the web chat, Feishu,
  Lark, web push), each `off` by default and followed live, no restart. Upgrading? Add
  `[notify] telegram = "all"` and `web = "all"` to keep the cards coming.
- **Real web push**, off by default: the workbench's 🔔 subscribes a browser (VAPID, a Service
  Worker, end-to-end encrypted); notifications even with no tab open; Setup → Ping tests it and lists
  the subscribed browsers. A manifest lets the workbench be pinned to a Home Screen (iOS web push).
- **The workbench from your LAN**, guarded: `BUDDY_WEB_TOKEN` (a login page, then a cookie) and
  `[web] hosts`, both applied without a restart.
- **zh-CN installs** don't offer Telegram or Lark (Setup shows them on request); doctor lists every
  outside endpoint in use (a fresh install: none).
- `/shift` and `/takeover` for long stories (renamed in the next release).
- Offline archives (buddy and pys together, installed with no network) are attached to this release.

## 20261112.6

- **Peers**: `allow` (local, private, public), resources by name (each machine keeps its own address),
  a self-description sent in the ping, the peers as a block in your global instructions (off by
  default); `buddy peer story`: one story on every machine.
- **Lanes** in level 2: who did what, where, in parallel (machine, session, subagent), with Attach to
  the session's pys window.
- **Feishu and Lark notify switch** (`[notify]`, off by default) and a count of message calls.
- `buddy scan feishu|lark`: an app by QR code on a buddy already set up, or an app you have, with a
  scan-to-enable link for its missing permissions.
- The `dbg` team role (tricky debugging across components).

## 20261112.5

- Thread replies a chat refuses go to the main chat instead, and doctor says why.

## 20261112.4

- A QR-scanned app writes to you first, so your chat is linked at once; unlinked chats can be linked
  from the Setup tab.

## 20261112.3

- `buddy service status`, `restart` and `uninstall`.

## 20261112.2

- A neutral example address in `buddy peer send --help`.
