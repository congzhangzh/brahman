"""Engines run one turn of an AI agent.

The agent owns the conversation (Claude Code keeps it under its session_id);
the buddy only remembers which session belongs to which chat.
"""

import json
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from . import procs

# A headless turn has nobody at a speaker: `speak` (the owner's voice wrapper) stays silent.
QUIET = {"SPEAK": "off"}
# The chat apps' secrets stay with the buddy: the agent, its Bash commands, hooks and MCP
# servers never see them (a prompt injection in a README could send its env away).
SECRETS = ("TELEGRAM_BOT_TOKEN", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "LARK_APP_ID",
           "LARK_APP_SECRET", "BUDDY_ALLOWED_IDS", "BUDDY_ALLOWED_OPEN_IDS")


def agent_env() -> dict:
    return {**{k: v for k, v in os.environ.items() if k not in SECRETS}, **QUIET}


@dataclass
class Reply:
    text: str
    session_id: str | None
    ok: bool
    denied: list[str] = field(default_factory=list)  # tools the agent wanted but was not allowed
    detail: str = ""  # on failure: the agent's own output, for the run record
    cost: dict = field(default_factory=dict)  # what the turn spent, as the engine reports it


class Engine(Protocol):
    def run(self, prompt: str, *, cwd: str, session_id: str | None = None,
            extra_env: dict | None = None) -> Reply: ...


class ClaudeCode:
    """Headless Claude Code (`claude -p`).

    Skills in ~/.claude/skills and <cwd>/.claude/skills load as in an interactive
    session. Headless mode cannot ask for approval, so a tool outside
    `allowed_tools` is denied, not prompted; denials are reported back.
    """

    def __init__(self, allowed_tools: list[str] = (), permission_mode: str = "default",
                 model: str = "", timeout: int = 1800, append_system_prompt: str = "",
                 disallowed_tools: list[str] = (), env=None, wrap=None):
        self.env = env or agent_env  # a long story's sessions get the gate's (gate.session_env)
        self.wrap = wrap  # and run in its sandbox (gate.sandbox): (cmd, cwd, env) -> (cmd, env)
        self.allowed_tools = list(allowed_tools)
        self.disallowed_tools = list(disallowed_tools)  # denied even when allowed_tools lets it in
        self.append_system_prompt = append_system_prompt
        self.permission_mode = permission_mode
        self.model = model
        self.timeout = timeout

    def live_cmd(self, *, session_id: str = "", new_id: str = "") -> list[str]:
        """The command of a long story's live session (long_live.py): one process, many turns,
        over the stream-json protocol; a permission it needs comes out as a request the buddy
        answers (`--permission-prompt-tool stdio`), not a silent denial. Resumes session_id,
        or starts the session new_id."""
        cmd = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--permission-prompt-tool", "stdio", "--permission-mode", self.permission_mode]
        cmd += ["--resume", session_id] if session_id else ["--session-id", new_id]
        if self.allowed_tools:
            cmd += ["--allowedTools", ",".join(self.allowed_tools)]
        if self.disallowed_tools:
            cmd += ["--disallowedTools", ",".join(self.disallowed_tools)]
        if self.model:
            cmd += ["--model", self.model]
        if self.append_system_prompt:
            cmd += ["--append-system-prompt", self.append_system_prompt]
        return cmd

    def run(self, prompt: str, *, cwd: str, session_id: str | None = None,
            extra_env: dict | None = None, kind: str = "chat", unit: str = "",
            tag: dict | None = None) -> Reply:
        """One turn. kind, unit and tag go to procs.run: the umbrella's slice, the unit's name
        (a long story's, so only one turn of it runs at a time) and what a later instance needs
        to deliver it after a restart (procs.adopt, then parse)."""
        cmd = ["claude", "-p", "--output-format", "json", "--permission-mode", self.permission_mode]
        resumed = bool(session_id)
        # A new session gets its id from us, not from the reply: a turn that times out
        # or crashes still names the session that holds its transcript.
        if session_id:
            cmd += ["--resume", session_id]
        else:
            session_id = str(uuid.uuid4())
            cmd += ["--session-id", session_id]
        if self.allowed_tools:
            cmd += ["--allowedTools", ",".join(self.allowed_tools)]
        if self.disallowed_tools:
            cmd += ["--disallowedTools", ",".join(self.disallowed_tools)]
        if self.model:
            cmd += ["--model", self.model]
        if self.append_system_prompt:
            cmd += ["--append-system-prompt", self.append_system_prompt]
        try:
            # The prompt goes in on stdin so text starting with "-" is never read as a flag.
            env = {**self.env(), **(extra_env or {})}
            if self.wrap:
                cmd, env = self.wrap(cmd, cwd, env)
            p = procs.run(cmd, input=prompt, cwd=cwd, timeout=self.timeout, env=env,  # STOP can kill it
                          kind=kind, unit=unit, tag={**(tag or {}), "session_id": session_id, "resumed": resumed})
        except subprocess.TimeoutExpired as e:
            partial = "".join(x.decode(errors="replace") if isinstance(x, bytes) else (x or "")
                              for x in (e.stderr, e.stdout))
            return Reply(f"timed out after {self.timeout}s", session_id, False, detail=partial)
        return self.parse(p, session_id, resumed)

    @staticmethod
    def parse(p, session_id: str, resumed: bool) -> Reply:
        """What a finished `claude -p --output-format json` said, as a Reply."""
        try:
            d = json.loads(p.stdout)
        except json.JSONDecodeError:
            err = (p.stderr or p.stdout).strip()
            why = (f"stopped: killed by signal {-p.returncode} (■ STOP?)" if p.returncode < 0
                   else f"claude exited with {p.returncode}")
            return Reply(err[-2000:] or why, session_id, False, detail=err)
        denied = sorted({x.get("tool_name", "?") for x in d.get("permission_denials") or []})
        u = d.get("usage") or {}
        # total_cost_usd is the whole session's so far, at list rates (modelUsage costBasis
        # "list"): an estimate of energy spent, not a bill. The run record turns it into this
        # turn's cost. usage, num_turns and duration_ms are this call's alone.
        cost = {"usd_session": d.get("total_cost_usd") or 0, "resumed": resumed,
                "turns": d.get("num_turns") or 0,
                "agent_ms": d.get("duration_ms") or 0,
                "tokens_in": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
                "tokens_cached": u.get("cache_read_input_tokens") or 0,
                "tokens_out": u.get("output_tokens") or 0}
        text, detail = d.get("result") or "", ""
        if d.get("is_error"):
            # A turn that stops early (subtype error_max_turns, error_during_execution) carries
            # no "result", only "errors"; without this the failure card and black box are empty.
            why = "; ".join(d.get("errors") or []) or d.get("subtype") or "the agent reported an error"
            text = text or f"{why} ({d.get('subtype', 'error')})"
            detail = json.dumps({k: d.get(k) for k in ("subtype", "terminal_reason", "stop_reason",
                                                       "errors", "num_turns", "permission_denials")},
                                ensure_ascii=False, indent=1)
        return Reply(text, d.get("session_id") or session_id, not d.get("is_error"), denied,
                     detail=detail, cost=cost)


class Codex:
    """Headless Codex (`codex exec --json`), read-only by default like the buddy's Claude Code.

    It starts a session (or resumes one by id) for one turn. The session's later turns go
    through `codex queue` (long_stories.py), which Codex's own daemon runs or hands to the
    terminal that holds the session.
    """

    def __init__(self, sandbox: str = "read-only", model: str = "", timeout: int = 1800, env=None, wrap=None):
        self.sandbox, self.model, self.timeout = sandbox, model, timeout
        self.env, self.wrap = env or agent_env, wrap

    def run(self, prompt: str, *, cwd: str, session_id: str | None = None,
            extra_env: dict | None = None, kind: str = "chat", unit: str = "",
            tag: dict | None = None) -> Reply:
        resumed = bool(session_id)
        cmd = ["codex", "exec"] + (["resume", session_id] if session_id else []) + [
            "--json", "--skip-git-repo-check", "-c", f'sandbox_mode="{self.sandbox}"']
        if not session_id:
            cmd += ["-C", cwd]
        if self.model:
            cmd += ["-m", self.model]
        cmd.append("-")  # the prompt on stdin, so text starting with "-" is never a flag
        try:
            env = {**self.env(), **(extra_env or {})}
            if self.wrap:
                cmd, env = self.wrap(cmd, cwd, env)
            p = procs.run(cmd, input=prompt, cwd=cwd, timeout=self.timeout, env=env,  # STOP can kill it
                          kind=kind, unit=unit, tag={**(tag or {}), "session_id": session_id, "resumed": resumed})
        except subprocess.TimeoutExpired:
            return Reply(f"timed out after {self.timeout}s", session_id, False)
        return self.parse(p, session_id, resumed)

    @staticmethod
    def parse(p, session_id: str | None, resumed: bool) -> Reply:
        """What a finished `codex exec --json` said, as a Reply."""
        said, usage, errors = [], {}, []
        for line in (p.stdout or "").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            item = e.get("item") or {}
            if e.get("type") == "thread.started":
                session_id = e.get("thread_id") or session_id
            elif e.get("type") == "item.completed" and item.get("type") == "agent_message":
                said.append(item.get("text", ""))
            elif e.get("type") == "turn.completed":
                usage = e.get("usage") or {}
            elif e.get("type") in ("turn.failed", "error"):
                errors.append(json.dumps(e)[:300])
        ok = p.returncode == 0 and bool(said) and not errors
        text = "\n\n".join(said) or "; ".join(errors) or (p.stderr or "").strip()[-2000:] \
            or f"codex exited with {p.returncode}"
        cost = {"tokens_in": usage.get("input_tokens", 0), "tokens_cached": usage.get("cached_input_tokens", 0),
                "tokens_out": usage.get("output_tokens", 0), "resumed": resumed}
        return Reply(text, session_id, ok, detail="" if ok else (p.stderr or "")[-2000:], cost=cost)


def long_engines(cfg: dict, env_file=None) -> dict:
    """The engines a long story's headless turns run on, by kind. A long story is a session
    the owner started to work in, so it may write: [long.claude] goes over [claude] (its keys
    win; model, timeout and the system prompt carry over) and [long.codex] sets Codex's
    sandbox. Without them a long story is as read-only as the chat.

    "claude-code/open" and "codex/open" are the same engines for a session the owner started
    open (gate.py): his own environment and files, the buddy's keys still hidden. Their tool
    rules are [long.claude] with [long.open.claude] over it ([long.open.codex] for Codex), so
    what an open session may run (git push, gh pr create) is the owner's own line in buddy.toml.
    Not there when [gate] open = false."""
    from .gate import open_allowed, open_env, open_wrap, sandbox, session_env

    long, wrap = cfg.get("long", {}), sandbox(cfg, env_file=env_file)
    claude = {**cfg.get("claude", {}), **long.get("claude", {})}
    out = {"claude-code": ClaudeCode(**claude, env=session_env, wrap=wrap),
           "codex": Codex(**long.get("codex", {}), env=session_env, wrap=wrap)}
    if open_allowed(cfg):
        opened = long.get("open", {})
        env, owrap = (lambda: open_env(env_file=env_file)), open_wrap(env_file=env_file)
        out["claude-code/open"] = ClaudeCode(**{**claude, **opened.get("claude", {})}, env=env, wrap=owrap)
        out["codex/open"] = Codex(**{**long.get("codex", {}), **opened.get("codex", {})}, env=env, wrap=owrap)
    return out
