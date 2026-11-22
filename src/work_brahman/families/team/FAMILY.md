---
name: team
description: The long-lived sessions of a kit (its release manager, its product owner, one member per area), their generations, how old each is and what each does now, on one live card per kit in the owner's chats. Use when a release manager starts, hands over or retires a member, or when the owner asks who is on a team.
---

# Family: team

A team is the *who* of the work; short stories are the *what*. A team has no steps and no
end, so it is not a story: roles join, age, hand over and retire, and the team goes on
(specs/team.md; the rules for members and generations are the global CLAUDE.md "Team and
Roles" and claude_md/global/team.md).

| Word | Meaning |
|---|---|
| team | one per kit, named after it: a roster of roles, each with its kind |
| role | a long-lived duty, carried by one live generation at a time; one file, one history, even when several teams name it |
| kind | `rm` (release manager, exactly one per team) · `po` (product owner, at most one, may serve several teams) · `member` (one area) |
| generation | the session carrying the role (`claude --name`): `server-a1` (g1) → `server-g2` |
| book | `role-<role>.md` in the role's project memory (`--book` names another file) |
| age | years (compactions, from the transcript) and ctx now (the status line) |

## Stages

| # | Stage | Leads | Executes | Where | Done when |
|---|---|---|---|---|---|
| 1 | **New**: a kit's team and its RM | RM | `buddy-team new KIT --rel NAME [--area --project --book]` | chat: a new card | the card is in the owner's chats |
| 2 | **Join**: a member joins (after the owner's go to start it), keyed by its lineage (NAME without `-g<N>`) | RM | `buddy-team join KIT NAME --role po\|comp\|rev\|dbg\|edu --area AREA …` (one po per team, a comp per area); a member another team has: its live NAME | the card(s) | its row shows |
| 3 | **Look**: the card follows what it observes | `buddy serve` | every 60 s: sessions, pys windows, ctx, years, books, running short stories | the card, only when the banded facts change | — |
| 4 | **Reply**: the owner asks the RM something | the owner | 💬 Reply under the card, quoting it, or `/team KIT <text>` | the RM's window gets one fixed line | `buddy-team check KIT` prints it once |
| 5 | **Hand over**: a generation is old (the owner decides) | RM | `buddy-team handover OLD NEW`: the same for an RM or a PO; `rename OLD NEW` keeps the generation; `set NAME --role --area` corrects | every card naming the member: the new row, the line `OLD (gN) → NEW · book …` | — |
| 6 | **Retire** | RM | `buddy-team retire KIT NAME [--note]`: off this card, the record kept; on no team any more, its generation retires and its lineage may return as `-g<N+1>` (not a team's RM: hand it over) | the card(s) | — |
| 7 | **Read** | anyone | `buddy-team show KIT` (exact ctx, now), `buddy-team list`, `/team`, `/team KIT` | terminal, chat | — |

## Rules

- **The team only records; it never acts.** It does not start, resume, message, retire or
  close a session: the RM does, with the owner's go per member, given in the RM's session. A
  reply on the card is a request, as on a short story.
- **Only the RM writes** its team's files (the owner's decision, 2026-11-07). A shared role's
  handover is done by the RM of the team where it first joined; the other RMs are told by
  SendMessage. Every card naming the role is redrawn.
- **Only what can't be observed is kept** (roles/<role>.json, <team>.json); everything else
  is looked at each time the card is drawn, and a source that can't be read shows `-`.
- **Edited on a change, never on a timer** (the Lark quota): an event above, or a change in
  the banded facts: a session gone or back, a window, years, age across `age_warn` (60 %),
  the book appearing, its running short stories, "also" another team. The exact ctx is as of
  the last edit.
- **A name is never reused**, and a generation's name never changes: resuming after a crash
  is the same generation. A session gone shows `gone`, a fact, not a retirement.
- **One `rm` per team, at most one `po`**; an RM's role is handed over, never retired or left.
- Code only, no tokens.

## Where it lives

- The board: `$XDG_STATE_HOME/work_brahman/teams/` (`<team>.json`, `roles/<role>.json`),
  `src/work_brahman/teams.py`; `buddy serve` runs its looker (`teams.start_looker`).
- Settings: `[families.team]` in buddy.toml: `age_warn = 60`, `channels = ["telegram", "web"]`
  (the channel names the card goes to; empty: every chat). Lark is left out until its quota is
  back (the owner's decision, 2026-11-07).
- From another directory or session: `scripts/buddy-team`, installed as `~/.local/bin/buddy-team`.
- A short story started by a team's session records `team` and shows `👥 <team>` beside `who`.
