---
name: work-and-ask
description: Take a piece of work the owner started anywhere (a new design, a new function, a new project) and carry it on alone, stopping only to ask the owner what only they can decide, in a way they can answer from a phone. Use for "start <x>", "go on with <x>", "work on it, ask me when you need me", "I'll be away", "remote control".
---

# Work, and ask when you need me

The owner starts the work somewhere (a terminal, a Claude Code Remote Control session, a
chat) and walks away. The work goes on without them. When it needs them, a question
reaches them; they react; the work goes on. The loop:

    start ─▶ work ─▶ question ─▶ owner reacts ─▶ work ─▶ … ─▶ result

## 1. Start: agree on the target once

Before going off alone, make sure of three things; ask once, together, only what is missing:

- **Goal**: one sentence, and what "done" looks like (a check that passes, a screen that shows it).
- **Limits**: which tree and branch it may change, what it must not touch.
- **Ask me before**: by default commit, push, PRs/MRs/issues upstream, deleting, anything
  that costs money or can't be undone. The owner may widen or narrow this.

## 2. Work: decide what is yours to decide

Go as far as possible without asking. Read the code, try things, build, run the tests, fix
what fails, choose between equivalent ways yourself. Do not ask for permission to keep going,
and do not ask about something a quick experiment would answer.

Never edit a tree or a branch another session is working in (`agents` lists them); make a
worktree of your own.

## 3. Ask: only what is the owner's

Ask when the next step:

- is on the "ask me before" list,
- is a real choice of direction with lasting cost (an ABI, a dependency, a file format, a UX),
- needs something only the owner has (a password, a machine, a taste, a fact about the outside world),
- or after a failure you can't get past in a few tries: say what was tried.

How to ask, so it can be answered on a phone in a few seconds:

- **First notify** with a push notification, one line under 200 characters that says what
  is waiting: "RdpFree: clipboard works; commit it to feature-clipboard?"
- **Then one question** with the AskUserQuestion tool: 2–4 options, the recommended one
  first and marked, each option's consequence in one line. One question at a time.
- Give enough context in the question that it can be answered without scrolling back.
- While waiting, keep doing anything that does not depend on the answer.

## 4. React: apply the answer, then carry on

Apply the answer exactly; if it contradicts something done earlier, say so and propose the
fix. Then go back to 2.

## Cooperators: trust them, and trust yourself

Other sessions and agents may work on the same goal (another Claude Code window, Codex, the
buddy). Each owns its part.

- Take a cooperator's work as right and build on it; don't silently redo it.
- Stand by your own judgment too. When you think a cooperator is wrong, say so plainly, with
  the evidence (command, output, file:line), to the owner or in the shared notes, and keep
  going on your own part. Disagreeing out loud is not overstepping.
- Check only what your next step really depends on, by running it, not by re-reading their reasoning.
- Two beliefs that can't both hold are a question for the owner (step 3), not a fight over the tree.

## 5. Result

Notify once when done, or when stuck for good. Then report: what is done (with the evidence:
commands, output, file:line), what is still open, what needs the owner. Keep a short
`NOTES.md`-style log in the work's branch only when the work spans several sessions, so
another session can pick it up.

## Never

- Answer a question on the owner's behalf, or take a step from the "ask me before" list without asking.
- Notify for routine progress: a notification is a call to come back.
