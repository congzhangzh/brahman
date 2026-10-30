---
name: long-story
description: One agent session (Claude Code or Codex) mirrored to a thread in the owner's chats, so he can follow it and talk to it from his phone, any time. The session's transcript is the single source of truth; the terminal or the phone drives it, one at a time. Use for "follow this session on my phone", "I'll go on from the phone", "start a session from chat".
---

# Family: long story

A long story is one agent session, for as long as it lives. The owner follows it in a thread
(a Lark thread, a Telegram forum topic in his DM with the buddy) and talks to it there. The
session's own transcript is the single source of truth; the thread is a mirror of it. Spec:
`specs/long-story.md`.

| Family | One story is | Lasts | In chat |
|---|---|---|---|
| short-story | one piece of pipeline-bound work | minutes to hours | one card, edited in place |
| long-story | one agent session | as long as the session lives | one thread |

## Stages

| # | Stage | Leads | Executes | Where | Done when |
|---|---|---|---|---|---|
| 1 | **Start** in a terminal | you | `BUDDY_LONG="<name>" claude` (in GNU screen, so the phone can type into it) | a new thread in each chat | the thread says which screen window holds it |
| 1 (Codex) | **Start** in a terminal | you | `buddy-long codex` (registers the terminal, then runs Codex; its first prompt claims the story) | a new thread in each chat, and a tab on the web | the thread says which screen window holds it |
| 1′ | **Start** from the phone | you | `/long new`: a card asks the agent (Claude, Codex), the folder (earlier stories', then your recent projects), then the task as a reply; or in a line, `/long new [claude\|codex] [folder] <task>` | a new thread; its first turn runs headless (`[long]`'s tools) | the thread says how to take it to a terminal |
| 2 | **Turn**: the session answers | the session | its Stop hook posts the turn's last message | the thread | one message per turn, its prompt as the subtitle |
| 3 | **Say**: you write in the thread | you | Codex: `codex queue`, which Codex's daemon hands to the terminal or runs itself. Claude Code: held by a terminal: typed into its window when idle, handed over by its Stop hook when busy; held by nobody: a headless turn of the same session (`claude -p --resume`, `[long]`'s tools) | the session | the reply says ⌨ typed / 📥 queued, or the headless turn posts its answer |
| 4 | **Wait**: it needs a person | the session | Claude Code: its Notification hook posts the dialog as the screen shows it; Codex: its PermissionRequest hook posts a card with ✅ Allow / ⛔ Deny and waits (45 s while a terminal holds it, 9 min when none does) | the thread, the card | Claude Code: you reply with a number, that key is pressed; Codex: your press is its answer, or the terminal asks once the wait ends |
| 5 | **Let go / take back** | you | closing the terminal (SessionEnd) leaves it to the thread; `BUDDY_LONG=1 claude --resume <id>` takes it back | the thread | — |

## Rules

- **One writer at a time.** The buddy never resumes a session a terminal still holds (its
  `claude` process is alive); what you write goes through that terminal's own input. A
  session nobody holds is resumed by the buddy, one turn at a time.
- **Type only when it is idle**: text typed into a busy terminal mixes with what it is doing,
  so it waits and the Stop hook hands it over (the session reads it as "the owner wrote …").
  The text goes in first and Enter after a short pause: Claude Code reads a burst of keys as
  a paste, where Enter is only a newline.
- **The screen has the last word.** A hook can be missed (a turn the dialog's "No" interrupts
  fires no Stop), so before typing, the buddy reads the window (screen's hardcopy): a dialog
  means waiting, a spinner line busy, anything else idle.
- **Light**: one message per turn, one per wait; nothing per tool call. The idle reminder
  ("waiting for your input") is not posted.
- **Opt-in per session**: only sessions started with `BUDDY_LONG` set have a story; for every
  other session the hook is one `sh` that exits at once.
- **Commands are code, not a model.** The buddy's own commands (`/help`, `/long`, `/short`, …)
  typed in a story's thread are answered there by their code; the session never sees them.
  Anything else, an engine's own command like `/compact` too, goes to the session.
- **A long story may change its own code** (the owner, 2026-10-29). Its headless turns run
  with `[long.claude]` / `[long.codex]` in buddy.toml (`engine.long_engines`): edits in its own
  folder, tests and builds, local commits. Pushing, merging, opening PRs and restarting
  services stay with the owner (not allowed, or in `disallowed_tools`). Without `[long]` it is
  as read-only as the chat. The buddy's main chat and its duties stay read-only (`[claude]`).

## Where it lives

- The board: `$XDG_STATE_HOME/work_brahman/long_stories/<number>.json` (`src/work_brahman/long_stories.py`).
- Codex's hooks run in its shared daemon, for every Codex session: `buddy-long hook --engine codex`
  goes on only for a session with a story (`long_stories/sessions/<id>`) or while a
  `buddy-long codex` waits to be claimed (`long_stories/pending/<pid>.json`). They are listed in
  `~/.codex/hooks.json` and must be trusted once with `/hooks` in Codex.
- The hooks: `scripts/buddy-long hook`, installed as `~/.local/bin/buddy-long` and listed in
  `~/.claude/settings.json` under `SessionStart`, `UserPromptSubmit`, `Stop`, `Notification`
  and `SessionEnd`:

  ```json
  {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "buddy-long hook", "timeout": 60}]}], "…": "the same for the other four"}}
  ```
- In chat: `/long` lists the stories, nothing more; `/long new` starts one with a card,
  `/long new [claude|codex] [folder] <what to do>` in one line.
