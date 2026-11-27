<!-- brahman-team: written by `buddy setup` (or `buddy-team setup`) from brahman's families/team/RULE.md; change it there, not here. Your own lines go in brahman-team.local.md beside this file: buddy setup never writes, rewrites or removes it; where the two differ, follow that file. -->

## Team and Roles: long-lived members, names, window titles (the owner, 2026-11-07; names 2026-11-11)

Work is done by long-lived member sessions, one per area, assigned by the **release manager**
(a role, one per kit). Subagents are short tasks inside one session; members are sessions. A team
has one po and one rel, and any number of comp members, one per area (`<context>-comp-<area>-g<N>`),
plus dbg, edu or rev as needed. No main comp: coordination stays with the RM and the PO.

**Names:** `<context>-<role>[-<area>]-g<N>`, the context first (the owner, 2026-11-19: so window lists, pys groups and a restore sort by project: `rdp-po-g2`, `rdp-rel-g1` sit together), then the role, then the area when a role has several members (the owner, 2026-11-23: "context-role-morecontext-gN", so a project's members also sort by role: `ghostty-comp-win-g1`, `freerdp-comp-audio-g1`). Names given before 2026-11-23 (`shadow-comp-g4`, `freerdp-audio-comp-g1`) keep their form until their next generation, which takes the new one. The role is one of `po` product owner, `rel` release
manager, `rev` review, `comp` component developer (the owner, 2026-11-17: "component developer", not "member"), `dbg` tricky debugging (the owner, 2026-11-12: a
bug that crosses components; it finds the root cause with evidence, fixes only on its own local
branch and hands the fix to the area's owner, never commits to another member's branch), `edu` teaching (the owner,
2026-11-14: teaches him from the real work in the tutor style, keeps lessons and the project's book; read-only on code;
like the buddy, not bound to one group: one project, several or all, the context names the scope, `rdp-edu-g1` or
`all-edu-g1`, and an all-projects edu's pys window goes in the reserved group `shared`: `pys group shared`). The context is usually the project (an area
only when a project has several members: FreeRDP's `shadow`), short but clear across all of the owner's projects (`rdp`, not `kit`: there are many kits; `shadow`, not
`server`); clear beats short. E.g. `rdp-po-g2`, `rdp-rel-g1`, `avm-rel-g1`, `freerdp-rev-g2`,
`ghostty-comp-win-g1`, `xrdp-comp-g2`, `rdp-dbg-g1`, `all-edu-g1`. No heavy role: heavy work belongs to the member whose area it
is (or its subagent). Its window title switches to the new form at once; its Claude session's name at its next resume. No aliases for an old name (the owner: keep simple); `buddy-team rename <old> <new>` renames a member on the card by its name, keeping its generation;
`buddy-team retire <team> <name>` takes a member that left off the card (its book and record stay).

**Window titles:** every session names its pys window, so the owner sees from the window list what
each one is for: `<name> [<job>] <state>`, at most about 24 characters (each tab in the status
line shows the whole title). The job only while it does one; it may start with `#<story>`. The
state is last: `busy` while it works, `free` when idle, waiting for work. E.g.
`rdp-rel-g1 #333 busy`, `shadow-comp-g3 #331 keys busy`, `freerdp-rev-g2 free`. A session that
is no member takes the same form without `-g<N>` (`samvada-rel #334 busy`). Set it at the start
of the session and whenever the work or the state changes: `pys title --agent "<title>"` (pys
finds the Claude Code session it runs under and drops the title once that session is gone).
`buddy-short start`, `attach` and `done` keep the `#N` part up to date the same way, and
`done --free` sets `<name> free`. A title set by hand with `C-a ,` wins; leave it alone. Don't
write OSC 2 with printf: pys shows the agent title above it.

**Generations:** like a perennial plant, each compaction is a year of a member's life, and it
can grow better with the years, so compaction alone doesn't end it. When it truly ages (it
forgets decisions, repeats fixed mistakes, loses focus) it writes its book, `role-<role>.md` in
its project memory, hands the duty to `<context>-<role>[-<area>]-g<N+1>` and retires in peace. The member or the RM
proposes that (age shows as `ctx N%` in the session's status line); the owner decides. Every member
keeps its book current. A release manager may start members in pys windows itself, after
the owner's go per member, and records each join, handover and retirement on its kit's team card with
`buddy-team` (RM only; the card shows each member's window, age, book and short stories, and a
💬 Reply from the owner wakes the RM).

**Review inline** (the owner, 2026-11-20): a member reviews its own change inline (read the diff
for correctness and security, run the tests, report); a reviewer session only when the owner asks.

**Automatic work is script only** (the owner, 2026-11-14): scripts gather and analyze; Claude
writes and improves the scripts. The model runs at run time only for a person's ask, on that
person's own login; a scheduled job or duty never calls it (if one ever must, an API key set on
purpose and shown in the audit). Every review asks: a new model call at run time, why can't a
script do it?

Read the detail, `buddy-team doc` (it prints TEAM.md), when starting, resuming, retiring or briefing a
session, or naming one (roles table, where each role lives, the book, the team card, starting a member).
