# Duty: stale-stories

Close a short story that will never end by itself: you pressed ■ Stop, nothing happened on it for a
day, and the session that ran it is gone, so nobody will ever read the stop and end it. Without this
the board keeps a dead story as "running" forever (the owner, 2026-11-17: "Close them + a rule").
Script only: no model, $0.

## Abilities

| Ability | Trigger | Kind | Interaction |
|---|---|---|---|
| Close stale stories | scheduler (`schedule`, e.g. hourly) | code | notify, only when it closed one |

## The rule

A story is closed, as **failed** with the note "stopped by the owner; the session was gone", when
all of these hold:

1. it is running and has a stop request (■ Stop on its card, ■ STOP in chat, or its parent's);
2. nothing happened on it for `quiet_hours` (default 24): no mark, note, reply or stop since;
3. its session is gone, with evidence on this machine:
   - Claude Code: `claude agents --json` answers and lists no session with the story's id, and the
     window it marked from, if it marked from one, no longer runs that process (same pid, same start
     time). The listing is your Claude Code's, so a session of another user or another
     `CLAUDE_CONFIG_DIR` reads as gone: the stop request and the quiet day keep that safe;
   - Codex: the daemon answers and knows no such thread.

The check is made again under the story's lock when it closes one: a mark that came in meanwhile
keeps it open. Closing a parent as failed asks its running children to stop (as ■ Stop on the
parent would); a child whose session is gone too closes a quiet day later, by the same rule.

Anything unsure keeps the story open and is only counted: a story marked on another machine, one
whose `by` names no session, a check that can't be asked. Each run's record (the workbench's
Level 1, `buddy runs`) says how many stories had a stop request, how many were quiet, which it
closed and which it was unsure of.

## Setup

```toml
[duties.stale-stories]
schedule = "17 * * * *"   # hourly
quiet_hours = 24          # optional
# chat_id: where the notice goes (default: your home chat)
```

`buddy duty stale-stories` runs one check by hand.
