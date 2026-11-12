## Team and Roles: long-lived members, names, window titles (the owner, 2026-11-07; names 2026-11-11)

Work is done by long-lived member sessions, one per area, assigned by the **release manager**
(a role, one per kit). Subagents are short tasks inside one session; members are sessions.

**Names:** `<role>-<context>-g<N>`. The role is one of `po` product owner, `rel` release
manager, `rev` review, `comp` a component's owner. The context is usually the project (an area
only when a project has several members: FreeRDP's `shadow`), short but clear across all of the owner's projects (`rdp`, not `kit`: there are many kits; `shadow`, not
`server`); clear beats short. E.g. `po-rdp-g2`, `rel-rdp-g1`, `rel-avm-g1`, `rev-freerdp-g2`,
`comp-shadow-g3`, `comp-xrdp-g2`. No heavy role: heavy work belongs to the member whose area it
is (or its subagent). A live member keeps its old name to the end of its generation.

**Window titles:** every session names its pys window, so the owner sees from the window list what
each one is for: `<name> [<job>] <state>`, at most about 24 characters (each tab in the status
line shows the whole title). The job only while it does one; it may start with `#<story>`. The
state is last: `busy` while it works, `free` when idle, waiting for work. E.g.
`rel-rdp-g1 #333 busy`, `comp-shadow-g3 #331 keys busy`, `rev-freerdp-g2 free`. A session that
is no member takes the same form without `-g<N>` (`rel-samvada #334 busy`). Set it at the start
of the session and whenever the work or the state changes: `pys title --agent "<title>"` (pys
finds the Claude Code session it runs under and drops the title once that session is gone).
`buddy-short start`, `attach` and `done` keep the `#N` part up to date the same way, and
`done --free` sets `<name> free`. A title set by hand with `C-a ,` wins; leave it alone. Don't
write OSC 2 with printf: pys shows the agent title above it.

**Generations:** like a perennial plant, each compaction is a year of a member's life, and it
can grow better with the years, so compaction alone doesn't end it. When it truly ages (it
forgets decisions, repeats fixed mistakes, loses focus) it writes its book, `role-<role>.md` in
its project memory, hands the duty to `<role>-<context>-g<N+1>` and retires in peace. The member or the RM
proposes that (age shows as `ctx N%` in the session's status line); the owner decides. Every member
keeps its book current. A release manager may start members in pys windows itself, after
the owner's go per member, and records each join, handover and retirement on its kit's team card with
`buddy-team` (RM only; the card shows each member's window, age, book and short stories, and a
💬 Reply from the owner wakes the RM).

Read `{detail}` when starting, resuming, retiring or briefing a session, or naming one (roles
table, where each role lives, the book, the team card, starting a member).
