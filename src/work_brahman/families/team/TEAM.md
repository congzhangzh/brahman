# Team and roles: long-lived members, generations, books (the owner, 2026-11-07)

The detail behind the team block in the global instructions (`~/.claude/CLAUDE.md`,
`~/.codex/AGENTS.md`), which `buddy-team setup` writes from brahman (the owner, 2026-11-11: "make it
global instruction and install by brahman"). Read it when starting, resuming, retiring or
briefing a session. Moved here from the owner's template repo (`claude_md/global/team.md`); brahman keeps it from now on. Change it here, then run `buddy-team setup` again.

## The shape

The owner's words: "prefer reuse session, like a team, we work together for a long time, know each
other, have professional parts, a stable relationship, and dynamic by release manager".

- **Members** are long-lived sessions, one per area. **Names (the owner, 2026-11-11):**
  `<role>-<context>-g<N>`: the context is usually the project, an area only when a project has several members (FreeRDP: `shadow`, `freerdp` for review), short but clear across all of the owner's projects (`rdp`, not
  `kit`: there are many kits; `shadow`, not `server`: xrdp is a server too); clear beats short; the role is
  one of:

  | Prefix | Role | Examples |
  |---|---|---|
  | `po` | product owner (priorities, the private plan) | `po-rdp-g2` |
  | `rel` | release manager, one per kit | `rel-rdp-g1`, `rel-avm-g1`, `rel-samvada-g1` |
  | `rev` | review: upstream PRs, the MR loop | `rev-freerdp-g2` |
  | `comp` | a component's owner | `comp-shadow-g3` (FreeRDP's shadow server, encoders, sesman, the hosts), `comp-freerdp-web-g9`, `comp-xrdp-g2`, `comp-avm-g1` |

  Short context words, so `<name> [<job>] <state>` (e.g. `comp-shadow-g3 #331 keys busy`) fits a pys title (~24 characters; when a clear name is longer, the work part shrinks) (the
  team block, "Window titles"). All the area's work goes to its member, whatever the topic.
  **No heavy role** (the owner, 2026-11-11): heavy work (an integration's build and ctest, containers)
  belongs to the member whose area it is, handed to its own subagent; the machine's one heavy
  job at a time still holds. **Renaming:** a live member keeps its old name (e.g.
  `freerdp-server-g3`) to the end of its generation; its successor and every new member take the
  new form.
- **The release manager (RM)** is a role too, one per kit (`app-release-kit`, `lib-release-kit`,
  `tool-release-kit` …). It assigns work by area, picks releases, pins, runs the rounds, and
  owns the integration branch (see git-flow.md, "The release kit coordinates a release bump").
- A long session answers better because it knows the code, the history and the owner; an old one is
  noisy and loses focus. The rules below keep both sides.

## Generations

The owner's picture: a child is born and grows up, carries the duty and makes life better; when he is
old he gets peace, the sun and his freedom; he writes a book for his son so the son grows up fast
and strong; the duty must pass to the next generation, because the old one has too much noise.

| Stage | When | What happens |
|---|---|---|
| Born | No live member owns the area | The RM asks the owner, then starts it with a brief (below) |
| Grown | Most of its life | Its area's work goes to it. After a crash or reboot it is resumed (`claude --resume <name>`), same name |
| Grown older | Each compaction is a year of its life | Still the same member, resumed as before. Like a perennial plant it may bloom more beautifully with the years than in its first: compaction alone ends nothing |
| Old | It forgets earlier decisions, repeats a fixed mistake or loses focus, beyond what its book can carry | Not resumed again: it writes the last chapter of its book, and the next generation takes the duty |
| Retired | After the handover | Set its title `<name> free`, no new work, closed after the owner's ok. The transcript stays (brahman-backup) |

**Who decides a handover.** A session can't always see its own age, least of all after a
compaction. The owner (2026-11-07): "like some multi-year plant, compaction like one year of life, and maybe more beautiful than the first generations; so this is decided by itself, the release manager and me". So: the member says so when it notices (it can't recall a decision, it repeats a fixed
mistake, its status line shows a high `ctx`); the RM watches its members and proposes; the owner decides,
because a successor needs his go to start and a retiree his ok to close. **Where to read the age:**
Claude Code's status line shows `ctx N% of <window>` at the bottom of each session's pane (the
template's statusline.sh); the RM reads a member's with `pys capture <wid>` (the line with `ctx`).

Names show the generation: `<role>-<context>-g<N>` (e.g. `comp-xrdp-g2`). The RM ages and hands over the same
way; its book is the kit's project memory.

## Where each role lives

A session's memory folder is fixed by the folder it starts in, so a role's home decides what it
remembers and with whom it shares memory.
- **Release manager:** its kit's folder (e.g. `~/projects/app-release-kit`).
- **Members:** their area's repo (e.g. `~/projects/xrdp`, `~/projects/FreeRDP`).
- **Product owner:** its own private plan repo (e.g. `~/projects/app-product-plan`, no remote),
  not the kit's folder (the kit is public and its memory is the RM's) and not one code repo (an
  all-in-one, cross-platform product spans them all). If products split later, each product's PO
  lives with its own private plan. (the owner, 2026-11-07: "good for all in one cross platform product".)
  An implementation-first project that is itself the MVP (e.g. this buddy's own live repo, private) keeps its PO in
  its own repo: there the plan and the code are both private and grow together. (the owner, 2026-11-07: "for
  implement first project like this one, stay in the private implement and try repo, as itself is mvp".)
- Moving a role's home is a generation change: the book is copied to the new memory folder (the
  old copy stays) and the next generation starts there.

## The book

`role-<role>.md` in that project's memory folder (`~/.claude/projects/<project>/memory/`), with
a pointer line in its MEMORY.md, so any session in that repo loads the pointer. It holds: what the
role owns; the owner's standing rules for it; open items with SHAs; its harnesses and where their
evidence lives; the traps it fell into. The member keeps it current all its life. The successor
reads it first and checks in with the RM before working.

## The team card (`buddy-team`)

One live card per kit in the owner's chats (Telegram and web while Lark is over its quota), one row per
role: kind (RM, PO, member), the generation, its pys window, its age (years = compactions, and
ctx now, ⚠️ from 60 %), its book, its running short stories, and the last handovers. It only
records; it never starts or stops a session. **Only the RM writes it**; a role in several teams
(a PO across kits) has one history, and its handover is run by the RM of the team it joined
first, the other RMs told by SendMessage. Role ids are global, so prefix them per kit (`rdp-rm`).

```bash
buddy-team new <kit> --rm <role> <session> --project <kit folder> [--book role-release-manager.md]
buddy-team join <kit> <session> --role <role> [--kind po] --area "<what it owns>" --project <repo>
buddy-team join <kit> --role <role> --kind po          # a role another kit has already
buddy-team handover <role> <old> <new>                 # the same for the RM's own role
buddy-team retire <role> <session> --note "<why>"      # the role ends; leave <kit> <role>: off one roster
buddy-team show <kit>                                  # exact ctx, now; list: every team
buddy-team check <kit>                                 # the owner's replies on the card, each once
```

When the owner replies on the card (💬 Reply, quoting it, `/team <kit> <text>`), the RM's window gets
`team <kit> has news from the owner: run buddy-team check <kit>`. A reply is a request: starting,
handing over or retiring still needs his go in the RM's session.

## Starting a member (the RM)

Ask the owner first, per member: the project, the name, the brief, the permission mode. Then:

```bash
cd ~/projects/<repo> && pys new -- bash                  # a window in that repo; prints its wid
pys send <wid> --enter "claude --name <role>-<context>-g<N> --permission-mode auto 'Read ~/.cache/rm/briefs/<date>-<name>.v<N>.md and start. You were started by the release manager <rm name> on the owner'\''s go.'"
```

- The window is a shell first, so it stays when Claude exits, like his other windows.
- The brief is a versioned file in `~/.cache/rm/briefs/` (goal, base and branch, done-criteria,
  who to report to, the rules), never a long shell argument. For a successor, the brief points
  at the role's book.
- Once it runs, the RM records it: `buddy-team join <kit> <name> --role <role> …`; at a
  handover, `buddy-team handover <role> <old> <new>` (the card's handover line names the book).
- Members report to the RM with SendMessage, set their pys title (`pys title --agent "<title>"`),
  never pick a release or push an integration branch, and need the owner's go to publish as usual.
- First member started this way: `xrdp-cq` (2026-11-07, pys window 12, auto mode).
