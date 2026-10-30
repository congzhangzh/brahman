# Duty: threads

Follow every open GitHub issue, pull request and discussion I take part in, and tell me in
chat when one turns to me: someone other than me (not a bot) spoke last. The code-review
duty watches my own PRs in depth; this one watches every conversation I'm in, for one
question only: whose turn is it? See `book/2-design.md` for where a duty sits.

## Abilities

| Ability | Trigger | Kind | Interaction |
|---|---|---|---|
| "Your turn" notice | scheduler (`schedule`, every 10 min) | code | notify |
| Daily list | the first check after `digest` (e.g. 09:00) | code | notify |
| `/threads` | chat command | code | the whole list, after a fresh check |

## The notice

Changes first, then the whole list:

1. one line per thread that turned to me: `↩ your turn: repo#N title (who, when)`;
2. the list of every thread, in two parts (聪哥, 2026-10-25): **Latest**, the threads
   active in the last `latest_days` (default 3), newest first; then **Most talked in**,
   all the others, the ones I spoke in most first (💬, the tool's `mine`: my comments,
   reviews and replies among the last ones fetched, plus 1 if I opened it). The turn stays
   a column. A row that changed since the last check is marked `*`.

A thread that turns to *them* (I answered) changes only its row; it posts nothing.

## Steps (one check)

1. **Fetch** through the L1 tool `threads --json` (`command`): GitHub GraphQL search
   `involves:<me>`, open, updated in the last 60 days; its own 10-minute cache.
2. **Compare** with the last snapshot: a thread turned to me if its turn is now `you` and
   either it wasn't before, or someone spoke again since.
3. **Skip** PRs the code-review duty watches: it already reports their new comments.
   They stay in the table.
4. **Post** the notice, only when something turned to me (or the daily list is due).
5. **Save** the snapshot.

The first check only learns: it posts how many threads it follows, and the list.

## Rules

- **Read-only.** The duty never comments or closes anything.
- **A failing tool changes nothing.** If `threads` fails, the snapshot stays and the notice
  is one ⚠️ line. On a cold cache (`"refreshing": true`) the check is silent; the next one
  has data.
- **Bots never make it my turn.** The tool skips accounts of type Bot, `*[bot]` and
  `THREADS_BOTS`.
- **"Your turn" means someone else spoke last**, not that they asked me something: a
  "thanks!" counts too.
- **No tokens.** `threads` uses `gh`'s own login. GitHub only.

## Data

`$XDG_STATE_HOME/work_brahman/threads.json`: `items` (url → kind, repo, number, title, url,
last speaker, at, turn), `checked`, `digest_day` (the day the daily list was sent).

## Config

```toml
[duties.threads]
schedule = "*/10 * * * *"
digest = "09:00"                  # optional: the whole list once a day, after this time
# command = ["threads", "--json"] # the L1 tool; any command printing the same JSON works
# chat_id: optional with [me]; notices go to every linked chat
```
