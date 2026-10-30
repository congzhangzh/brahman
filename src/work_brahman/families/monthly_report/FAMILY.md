---
name: monthly-report
description: Once a month, what our own work did: upstream MRs merged, opened and still open, a Gantt chart of the work in flight, whose turn each one is, and the buddy's own runs and cost. One story per month. Use for "monthly report", "what did I do this month", "report for <month>".
---

# Family: monthly report

A story is one month. The reader is the owner, in chat (Lark, and every linked chat). The
report is drawn from what is already recorded; nobody has to type anything into it.

## Stages

| # | Stage | Leads | Executes | Where | Done when |
|---|---|---|---|---|---|
| 1 | **Gather**: every PR/MR of mine updated in the month | — | code | GitHub (gh GraphQL), GitLab (anonymous REST), run records, asks | the lists are in; a forge that failed is named with ⚠️ |
| 2 | **Analyse**: upstream vs my own repos; merged, opened, closed, open; turns | — | code, with the upstream-mr family's turn rule | — | the numbers are in the headline |
| 3 | **Chart**: what matters first | — | code | text in the card | it fits a phone |
| 4 | **Post** on the 1st, 09:00 (`[families.monthly-report]`) | — | scheduler → `buddy family monthly-report` | chat | the card is in every linked chat |
| 5 | **React**: read it; answer what it asks | you | — | chat | each "your turn" and "ping, wait or drop?" line is handled, or left on purpose |

## What the card shows, in this order

1. The headline: merged, opened, closed, still open; how many are your turn.
2. Merged, by repository, and a bar per week of the month (the rhythm of the month).
3. The timeline of the work **still in flight**: one row per open MR, from the day it was
   opened (or the 1st) to today. `█` open, `M` merged, `x` closed, `>` still open.
4. Your turn: the open MRs waiting on you, and why.
5. Asked: MRs open 30+ days with the turn on upstream: ping, wait or drop?
6. The buddy itself: runs, failures, list-price dollars, questions answered.

`/report` shows the month so far; `/report 2026-09` a past month; `all` adds every merged
MR and every row of the timeline. `buddy family monthly-report --print` prints it in a
terminal.

## Honest limits

- **Turns are today's.** For a past month, "open" and "your turn" are the MRs' state now,
  not at the end of that month: no record keeps the history of turns yet.
- GitHub: the first 100 PRs updated in the month (a ⚠️ line says when there were more).
  GitLab: only the projects listed under `[duties.code-review] gitlab`.
- The buddy's run records began on 2026-10-24; earlier months have none.
- Your own GitLab replies are not recorded, so a GitLab comment by someone else counts as
  your turn (see the upstream-mr family).

## May vary per story

A month with something special (a release, a conference, time off) gets a line by hand in
chat, after the card. When the same kind of line comes back month after month, it becomes
a section here.
