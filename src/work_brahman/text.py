"""What the buddy says, in English or Chinese: one catalog for every channel and the page.

The language is `language` in the [buddy] section of buddy.toml, "zh-CN" (the default) or
"en", read once when the process starts (cli.main calls set_language). Every message the
buddy composes goes through t():

    t("short.sent", n=12)   -> "✓ Sent to short story #12: its executor reads it at its next step."
                               "✓ 已发给短篇故事 #12：执行者会在下一步读到。"

What comes from outside stays as it came: PR titles, commit subjects, comments, the agent's
answers, what the owner types, git's and the forges' own error text.

Keys starting with "page." are the workbench page's words (static/index.html): web.py hands
them to the page, which fills in their {fields} itself. With language = "en", every
English string here is the one the code said before the catalog existed, byte for byte
(tests/test_text.py checks that against tests/golden_en.json).
"""

import logging
import string
import time
from datetime import datetime

log = logging.getLogger("buddy")

LANGUAGES = ("en", "zh-CN")
DEFAULT = "zh-CN"
_lang = DEFAULT


def set_language(lang: str | None) -> str:
    """Use `lang` from now on; none means the default, an unknown one is logged and the
    default used."""
    global _lang
    lang = (lang or DEFAULT).strip()
    if lang not in LANGUAGES:
        log.warning("language %r is not one of %s: using %s", lang, ", ".join(LANGUAGES), DEFAULT)
        lang = DEFAULT
    _lang = lang
    return _lang


def language() -> str:
    return _lang


def t(key: str, /, **fields) -> str:
    """The text for key in the current language, with its {fields} filled in. In Chinese a
    field that starts or ends with a Latin letter or digit gets a space where it meets a
    Chinese character ("从 Lark 停止", "从网页停止"), so the templates write none there."""
    en, zh = CATALOG[key]
    if _lang != "zh-CN":
        return en.format(**fields) if fields else en
    if not fields:
        return zh
    out = ""
    for literal, field, spec, conv in _FORMAT.parse(zh):
        out = _spaced(out, literal)
        if field is not None:
            out = _spaced(out, format(_FORMAT.convert_field(fields[field], conv), spec or ""))
    return out


_FORMAT = string.Formatter()


def _spaced(a: str, b: str) -> str:
    """a + b, with a space between a Chinese character and a Latin letter or digit."""
    if a and b and ((_han(a[-1]) and _latin(b[0])) or (_latin(a[-1]) and _han(b[0]))):
        return f"{a} {b}"
    return a + b


def _han(ch: str) -> bool:
    return "\u3400" <= ch <= "\u9fff"


def _latin(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def tn(key: str, n: int, /, **fields) -> str:
    """t() for a count: key.one when n is 1, else key.many (Chinese has one form for both)."""
    return t(f"{key}.{'one' if n == 1 else 'many'}", n=n, **fields)


def app(name: str, cap: bool = True) -> str:
    """A channel's name in a sentence: "Lark", "Telegram", "Web" (or as written, cap=False)."""
    if _lang == "zh-CN":
        return APPS.get(name, name)
    return name.capitalize() if cap else name


def workflow(name: str) -> str:
    """A workflow's name as people read it (a job, duty or family, or "chat"): the catalog's
    "workflow.<id>", else the id itself, so a job new in buddy.toml still shows. Configs, logs,
    run records and the Base keep the id."""
    key = f"workflow.{name}"
    return t(key) if key in CATALOG else name


def task(key: str) -> str:
    """A scheduler task ("duty:code-review", maybe with an act after a space) in a sentence:
    as it is in English, by its workflow's name in Chinese."""
    return key if _lang != "zh-CN" else workflow(key.split(" ", 1)[0].split(":", 1)[-1])


def join(items) -> str:
    """A list in a sentence: "a, b" or "a、b"."""
    return t("sep").join(items)


def page(lang: str | None = None) -> dict[str, str]:
    """The page's words in lang (default: the current language), without the "page." prefix,
    the workflows' names as "workflow.<id>", and the SOP labels (runs.SOPS, which the page
    draws) as "label." + the English label."""
    i = 1 if (lang or _lang) == "zh-CN" else 0
    words = {k[5:]: v[i] for k, v in CATALOG.items() if k.startswith("page.")}
    words |= {k: v[i] for k, v in CATALOG.items() if k.startswith("workflow.")}
    return words | {"label." + k[4:]: v[i] for k, v in CATALOG.items() if k.startswith("sop.")}


# Dates. English keeps the strftime formats the code always used; Chinese spells them out.

WEEKDAYS = "一二三四五六日"  # tm_wday: 0 is Monday


def stamp(kind: str) -> str:
    """Now, for a subtitle: kind "day_time" (Mon 09:00), "day_date_time" (Mon 2026-10-26
    09:00) or "day_date" (Mon 26 Oct)."""
    if _lang != "zh-CN":
        return time.strftime({"day_time": "%a %H:%M", "day_date_time": "%a %Y-%m-%d %H:%M",
                              "day_date": "%a %d %b"}[kind])
    tm = time.localtime(time.time())
    wd = f"周{WEEKDAYS[tm.tm_wday]}"
    return {"day_time": f"{wd} {tm.tm_hour:02d}:{tm.tm_min:02d}",
            "day_date_time": f"{tm.tm_year}-{tm.tm_mon:02d}-{tm.tm_mday:02d} {wd} {tm.tm_hour:02d}:{tm.tm_min:02d}",
            "day_date": f"{tm.tm_mon}月{tm.tm_mday}日 {wd}"}[kind]


def month_year(d: datetime) -> str:
    return f"{d.year}年{d.month}月" if _lang == "zh-CN" else f"{d:%B %Y}"


def month_day(d: datetime) -> str:
    return f"{d.month}月{d.day}日" if _lang == "zh-CN" else f"{d:%b %d}"


def month_abbr(d: datetime) -> str:
    return f"{d.month}月" if _lang == "zh-CN" else f"{d:%b}"


APPS = {"lark": "Lark", "feishu": "飞书", "telegram": "Telegram", "web": "网页", "chat": "聊天"}

# key: (English, Chinese). Grouped by the module that says it.
CATALOG: dict[str, tuple[str, str]] = {
    # Shared words
    "sep": (", ", "、"),
    "with_note": ("{head}: {note}", "{head}：{note}"),
    "seconds": ("{n} s", "{n} 秒"),
    "denied": ("🔒 not allowed here: {tools}", "🔒 此处不允许：{tools}"),
    "failed": ("⚠️ Failed", "⚠️ 失败"),
    "truncated": ("…(truncated)", "…（已截断）"),
    "ack": ("Got it", "收到"),
    "btn.stop": ("■ Stop", "■ 停止"),
    "btn.resume": ("▶ Resume", "▶ 恢复"),
    "btn.reply": ("💬 Reply", "💬 回复"),
    "btn.send": ("💬 Send", "💬 发送"),
    "btn.stop_key": ("■ STOP", "■ 停止"),
    "btn.leave": ("Leave it", "先不管"),
    "btn.cancel": ("✖ Cancel", "✖ 取消"),
    "kind.job": ("job", "任务"),
    "kind.duty": ("duty", "职责"),
    "kind.family": ("family", "家族"),

    # Workflows by their id in buddy.toml (text.workflow): English shows the id
    "workflow.morning-prs": ("morning-prs", "早晨巡查"),
    "workflow.code-review": ("code-review", "代码Review"),
    "workflow.threads": ("threads", "待响应的任务"),
    "workflow.chat": ("chat", "专项沟通"),
    "workflow.short-story": ("short-story", "短链路任务"),  # the workbench's names (2026-11-10, the owner)
    "workflow.long-story": ("long-story", "完整 Claude/Codex"),
    "workflow.team": ("team", "团队"),

    # Channels: Lark (feishu.py), Telegram, the web chat
    "reply.placeholder": ("A reply to the executor", "给执行者的回复"),
    "reply.tg_placeholder": ("Your reply", "你的回复"),
    "topic.lark_card": ("A conversation with its own session, apart from the main one. Write in this "
                        "card's thread; the main chat goes on as before.",
                        "一段单独的对话，有自己的会话，与主对话分开。请在这张卡片的话题里留言，主聊天照常进行。"),
    "topic.lark_first": ("Write here.", "请在这里留言。"),
    "topic.tg_home_name": ("🏠 Main", "🏠 主线"),
    "topic.tg_home": ("The main conversation: the same one as in Lark and on the web. Write here "
                      "for anything that needs no topic of its own; cards and digests land here too. "
                      "A new topic: /topic <name>.",
                      "主对话，和 Lark、网页上的是同一个。不需要单独话题的事都可以写在这里；卡片和日报也会发到这里。"
                      "新开话题：/topic <名字>。"),
    "topic.tg_first": ("A conversation with its own session, apart from the main one. Write here.",
                       "一段单独的对话，有自己的会话，与主对话分开。请在这里留言。"),
    "topic.web": ("A conversation with its own session, apart from the main one.",
                  "一段单独的对话，有自己的会话，与主对话分开。"),
    "topic.default": ("topic {n}", "话题 {n}"),

    # The bot (bot.py)
    "help": ("""/new  start a fresh session in this chat (in a topic: that topic's session)
/topic NAME  a new conversation with its own session, beside this one (a Lark thread, a web tab)
/stop stop everything that is running or waiting, and pause the schedule (also ■ STOP)
/resume  let the schedule run again
/help this text
Anything else goes to Claude Code, in the same session as your last message in this chat.""",
             """/new  在这个聊天里开始新的会话（在话题里：该话题的会话）
/topic 名称  在这段对话旁边开一段新对话，有自己的会话（Lark 里是话题，网页上是标签页）
/stop 停止一切正在运行或等待的工作，并暂停计划任务（也可以按 ■ 停止）
/resume  让计划任务重新运行
/help 显示这段说明
其他消息都交给 Claude Code，与你在这个聊天里的上一条消息同属一个会话。"""),
    "bot.pong": ("🏓 pong {code} received: {channel} works both ways (the Setup tab shows it).",
                 "🏓 收到 pong {code}：{channel} 双向都通（设置页可以看到）。"),
    "bot.pong_button": ("🏓 Pong pressed ({code}): {channel}'s card buttons work (the card callback).",
                        "🏓 已按 Pong（{code}）：{channel} 的卡片按钮可用（卡片回传交互正常）。"),
    "long.thread_hint": ("💡 This went to the main conversation. To continue {story}, reply in its thread.",
                         "💡 这条消息进了主对话。要继续 {story}，请在它的话题里回复。"),
    "bot.ping_old": ("🏓 That ping card is an old one: send a new ping from the Setup tab.",
                     "🏓 这是旧的 ping 卡片：请从设置页重新发送 ping。"),
    "bot.not_paired": ("Not paired. On the buddy's machine run this, then restart the buddy:\n"
                       "buddy allow {sender}{chat}",
                       "尚未配对。在运行 buddy 的机器上执行下面这行，然后重启 buddy：\n"
                       "buddy allow {sender}{chat}"),
    "bot.text_only": ("Only text messages for now.", "目前只支持文字消息。"),
    "bot.topic_open": ("🧵 Topic {id} \"{name}\" is open, with a session of its own: in Lark, write in "
                       "its thread; on the web, in its tab.",
                       "🧵 话题 {id}「{name}」已开启，有自己的会话：在 Lark 里请在它的话题中留言，在网页上请在它的标签页里留言。"),
    "bot.working": ("⏳ Working…", "⏳ 处理中…"),
    "bot.queued": ("⏳ Queued behind the running turn.", "⏳ 排在正在进行的一轮之后。"),
    "bot.new": ("New session: the next message starts fresh.", "新会话：下一条消息将重新开始。"),
    "bot.error": ("⚠️ buddy error: {e}", "⚠️ buddy 出错：{e}"),
    "bot.then": ("{kind} **{name}**: {said}", "{kind} **{name}**：{said}"),
    "bot.answered_on": ("answered on {where}", "在{where}上回答"),
    "bot.mirror": ("↔ from {where}", "↔ 来自{where}"),
    "bot.short_how.telegram": ("answer here", "请直接在这里回答"),
    "bot.short_how.other": ("quote this message (long-press → Reply) and write",
                            "引用这条消息（长按 → 回复）后输入"),
    "bot.short_prompt": ("💬 Short story #{n}: {how}. Its executor reads it at its next step. "
                         "(Or send: /short {n} <text>)",
                         "💬 短篇故事 #{n}：{how}。执行者会在下一步读到。（也可以发送：/short {n} <文字>）"),
    "stop.head": ("■ **Stopped from {where}.** ", "■ **已从{where}停止。** "),
    "stop.killed.one": ("{n} process killed", "终止了 {n} 个进程"),
    "stop.killed.many": ("{n} processes killed", "终止了 {n} 个进程"),
    "stop.tasks": (" ({tasks})", "（{tasks}）"),
    "stop.closed.one": (", {n} question closed", "，关闭了 {n} 个问题"),
    "stop.closed.many": (", {n} questions closed", "，关闭了 {n} 个问题"),
    "stop.stories.one": (", stop asked of short story {list}", "，已请求短篇故事 {list} 停止"),
    "stop.stories.many": (", stop asked of short stories {list}", "，已请求短篇故事 {list} 停止"),
    "stop.end": (". ", "。"),
    "stop.paused": ("The schedule starts nothing new until you resume.",
                    "在你恢复之前，计划任务不会启动任何新的运行。"),
    "resume.done": ("▶ **Resumed from {where}.** The schedule runs again.",
                    "▶ **已从{where}恢复。** 计划任务重新运行。"),
    "resume.none": ("▶ Nothing was paused.", "▶ 没有暂停任何东西。"),

    # Asks (asks.py)
    "ask.unknown": ("This question is no longer known.", "这个问题已不存在。"),
    "ask.already": ("Already {said}.", "已{said}。"),
    "ask.bad_choice": ("That choice is not one of the options.", "这个选择不在选项之中。"),
    "ask.stopped_from": ("stopped from {where}", "从{where}停止"),
    "ask.answered_on": ("answered on {where}: {label}", "在{where}上回答：{label}"),
    "ask.settled": ("✓ answered on {where}: {label}", "✓ 已在{where}上回答：{label}"),
    "ask.stopped_label": ("stopped", "已停止"),
    "ask.stopped_line": ("■ stopped from {where}: no answer needed", "■ 已从{where}停止：无需回答"),
    "ask.title": ("buddy asks", "buddy 提问"),
    "ask.job_failed": ("{name} failed", "{name} 失败"),
    "ask.run_again": ("↻ Run it again", "↻ 再运行一次"),

    # Short stories (short_stories.py, families/short_story)
    "short.title": ("{head} short story #{n} {title}", "{head} 短篇故事 #{n} {title}"),
    "short.ev.started": ("started", "开始"),
    "short.ev.expected": ("expected too: {marks}", "还需要：{marks}"),
    "short.ev.failed": ("❌ failed", "❌ 失败"),
    "short.ev.done": ("🏁 done", "🏁 完成"),
    "short.ev.stop": ("■ stop asked from {where}", "■ 已从{where}请求停止"),
    "short.hours": ("{h} h {m} min", "{h} 小时 {m} 分钟"),
    "short.minutes": ("{m} min", "{m} 分钟"),
    "short.stop_asked": ("■ **Stop asked** from {where} `{at}`: the executor stops at its next step",
                         "■ **已请求停止**，来自{where} `{at}`：执行者会在下一步停下"),
    "short.unread.one": ("💬 {n} reply not read yet", "💬 {n} 条回复尚未读取"),
    "short.unread.many": ("💬 {n} replies not read yet", "💬 {n} 条回复尚未读取"),
    "short.not_planned": (" *(not planned)*", " *（计划外）*"),
    "short.here": ("  ◀ **here now**", "  ◀ **现在在这里**"),
    "short.loop.round": ("{k}/{n}", "第 {k}/{n} 轮"),
    "short.loop.rounds": ("a loop has 2 to {max} rounds: \"name ×3\" or \"name × a, b, c\": {spec}",
                          "循环有 2 到 {max} 轮：\"名字 ×3\" 或 \"名字 × a, b, c\"：{spec}"),
    "short.loop.no_round": ("{name} has no round {round} (it has {n})", "{name} 没有第 {round} 轮（共 {n} 轮）"),
    "long.not_home": ("A long story may edit files in its folder, so not {cwd} (your home or /): "
                      "name a project folder, e.g. /newlong ~/src/app <task>",
                      "长篇故事会改它目录里的文件，所以不能用 {cwd}（你的主目录或 /）："
                      "请指定一个项目目录，例如 /newlong ~/src/app <任务>"),
    "short.loop.try": ("try {k}", "第 {k} 次"),
    "short.loop.earlier": ("… {n} earlier tries", "… 之前还有 {n} 次"),
    "short.loop.tries": ("{name} has had {max} tries: more is another short story",
                         "{name} 已经试了 {max} 次：再多就是另一个短篇故事了"),
    "short.loop.open_round": ("{name} is an open loop: a try is never written over, so no --round",
                              "{name} 是开放循环：每次尝试都不会被覆盖，所以不用 --round"),
    "short.loop.not_loop": ("{name} is not a loop (a mark like \"soak ×3\")",
                            "{name} 不是循环（像 \"soak ×3\" 这样的标记）"),
    "short.fork.not_taken": (" *(not taken)*", " *（未走）*"),
    "short.fork.options": ("a fork has 2 to {max} options split by |: {spec}",
                           "一个分支有 2 到 {max} 个选项，用 | 分开：{spec}"),
    "short.fork.steps": ("an option has up to {max} steps (more is another short story): {spec}",
                         "一个选项最多 {max} 步（更多就是另一个短篇故事）：{spec}"),
    "short.fork.not_fork": ("{name} is not a fork (a mark like \"CI green? yes | no=fix\")",
                            "{name} 不是分支（分支像 \"CI green? yes | no=fix\"）"),
    "short.fork.no_option": ("{name} has no option {took} (it has {options})",
                             "{name} 没有选项 {took}（它有 {options}）"),
    "short.ev.child": ("↳ child story #{n} started for {step}: {title}", "↳ 子故事 #{n} 已开始，负责 {step}：{title}"),
    "short.ev.child_ended": ("↳ {icon} child story #{n} ({step})", "↳ {icon} 子故事 #{n}（{step}）"),
    "short.child.up": ("↑ part of #{n} {title} · {step}", "↑ 属于 #{n} {title} · {step}"),
    "short.child.expected": (" → *a child story*", " → *一个子故事*"),
    "short.child.stopped": ("stopped", "已停止"),
    "short.child.where": ("parent {n}", "父故事 {n}"),
    "short.child.ref": ("a parent is \"<number>:<step>\", or \"<number>:<step>:<round>\" for a loop: {ref}",
                        "父故事写作 \"<编号>:<步骤>\"，循环则写 \"<编号>:<步骤>:<轮次>\"：{ref}"),
    "short.child.one_level": ("short story #{n} is itself a child (of #{parent}): a child has no children of its own",
                              "短篇故事 #{n} 本身是子故事（属于 #{parent}）：子故事不能再有子故事"),
    "short.child.no_step": ("short story #{n} has no step {step} (it has {steps})",
                            "短篇故事 #{n} 没有步骤 {step}（现有：{steps}）"),
    "short.child.no_free_round": ("{name} has no round left without a child story",
                                  "{name} 已没有尚未关联子故事的轮次"),
    "short.child.taken": ("{name} already has child story #{child}: one child per step (a retry is a loop, \"name ×?\")",
                          "{name} 已有子故事 #{child}：每个步骤只有一个子故事（重试请用循环 \"名称 ×?\"）"),
    "short.child.settled": ("{name} is already {status}: a child story does a step still to do",
                            "{name} 已是 {status}：子故事只能负责尚未完成的步骤"),
    "short.child.running": ("short story #{n} still has child stories running ({children}): end them first, or `done --failed` to stop them",
                            "短篇故事 #{n} 还有子故事在进行（{children}）：先结束它们，或用 `done --failed` 让它们停止"),
    "gate.no_task": ("a session needs a task: say what to do", "会话需要一个任务：说明要做什么"),
    "gate.open_off": ("🔓 open sessions are turned off on this buddy ([gate] open = false): it runs 🔒 sandboxed only",
                      "这个 buddy 关掉了 🔓 开放会话（[gate] open = false）：只能 🔒 沙盒运行"),
    "gate.too_long": ("the task is {n} characters; a task is at most {max} (a message, not a file)",
                      "任务有 {n} 个字符；任务最多 {max} 个字符（是消息，不是文件）"),
    "long.short_context": ("(This session works on short story #{n}, a child story of {parent}. "
                           "Mark its steps with `buddy-short mark {n} \"<step>\"` and end it with "
                           "`buddy-short done {n}`, or `--failed --note \"<why>\"`. $BUDDY_SHORT is {n}.)",
                           "（本会话负责短篇故事 #{n}，它是 {parent} 的子故事。用 `buddy-short mark {n} \"<步骤>\"` "
                           "标记步骤，用 `buddy-short done {n}` 结束，或 `--failed --note \"<原因>\"`。$BUDDY_SHORT 为 {n}。）"),
    "short.none_running": ("No short story is running.", "没有正在进行的短篇故事。"),
    "short.none": ("No short stories yet.", "还没有短篇故事。"),
    "short.board_line": ("{title} — {done}/{total}, {elapsed}; last: {last}",
                         "{title} — {done}/{total}，{elapsed}；最近：{last}"),
    "short.sent": ("✓ Sent to short story #{n}: its executor reads it at its next step.",
                   "✓ 已发给短篇故事 #{n}：执行者会在下一步读到。"),
    "short.help": ("the short stories still running (/short all: every one, /short 12: one card, "
                   "/short 12 <text>: a reply to its executor, /short 12 stop)",
                   "仍在进行的短篇故事（/short all：全部，/short 12：一张卡片，"
                   "/short 12 <文字>：回复它的执行者，/short 12 stop：请求停止）"),
    "short.about": ("work that waits on pipelines between its steps; sessions start it",
                    "步骤之间要等待流水线的工作，由会话发起"),
    "short.started_by": ("started by {who}", "由{who}发起"),
    "short.a_session": ("a session", "某个会话"),
    "short.no_such": ("No short story #{n}.", "没有短篇故事 #{n}。"),
    "team.title": ("👥 Team {team}", "👥 团队 {team}"),
    "team.kind.rm": ("RM", "RM"),
    "team.kind.po": ("PO", "PO"),
    "team.kind.member": ("·", "·"),
    "team.kindname.rm": ("RM", "RM"),
    "team.kindname.po": ("PO", "PO"),
    "team.kindname.member": ("member", "成员"),
    "team.ev.new": ("team started, its RM {rm}", "团队成立，RM 为 {rm}"),
    "team.ev.join": ("{who} joined ({kind})", "{who} 加入（{kind}）"),
    "team.ev.leave": ("{who} left this team", "{who} 离开本团队"),
    "team.ev.handover": ("{old} (g{gen}) → {new} · book {book}", "{old}（g{gen}）→ {new} · 交接手册 {book}"),
    "team.ev.retire": ("{who} (g{gen}) retired, the role ends", "{who}（g{gen}）退休，角色结束"),
    "team.years": ("{n}y", "{n}年"),
    "team.part": ("(part)", "（子故事）"),
    "team.no_window": ("no window", "无窗口"),
    "team.gone": ("gone", "已离开"),
    "team.also": ("also {teams}", "也在 {teams}"),
    "team.empty": ("Nobody on this team yet.", "本团队还没有成员。"),
    "team.unread": ("💬 replies not read yet by the RM: {n}", "💬 RM 尚未读到的回复：{n}"),
    "team.as_of": ("as of {at}", "截至 {at}"),
    "team.board_line": ("{team} — RM {rm}, {n} roles", "{team} — RM {rm}，{n} 个角色"),
    "team.none": ("No teams yet.", "还没有团队。"),
    "team.help": ("the teams (/team NAME: its card, /team NAME <text>: a reply to its RM)",
                  "各团队（/team 名称：它的卡片，/team 名称 <文字>：回复它的 RM）"),
    "team.about": ("long-lived sessions, one per area, and the generations that carry them",
                   "长期会话，每个领域一个，以及承担它们的世代"),
    "team.sent": ("✓ Sent to the RM of team {team}: it reads it at its next step.",
                  "✓ 已发给团队 {team} 的 RM：它会在下一步读到。"),
    "team.no_such": ("No team {team}.", "没有团队 {team}。"),
    "bot.team_prompt": ("💬 Team {team}: {how}. Its RM reads it at its next step.",
                        "💬 团队 {team}：{how}。它的 RM 会在下一步读到。"),
    "short.ended": ("Short story #{n} has ended; nobody will read it.", "短篇故事 #{n} 已结束，没有人会读到。"),
    "short.stop_sent": ("■ Stop asked of short story #{n}.", "■ 已请求短篇故事 #{n} 停止。"),
    "short.ev.asked": ("❓ question {q}: {text}", "❓ 问题{q}：{text}"),
    "short.ev.answered": ("✔️ answer {q} from {where}: {text}", "✔️ 来自{where}的回答{q}：{text}"),
    "short.q.open": ("❓ **Question {q}, waiting for you:** {text}", "❓ **问题{q}，等你回答：**{text}"),
    "short.q.hint": ("*What you write with 💬 answers question {q}.*", "*用 💬 写下的话会回答问题{q}。*"),
    "short.q.hint_options": ("*Press an option, or answer question {q} in your own words with 💬.*",
                             "*点一个选项，或用 💬 以你自己的话回答问题{q}。*"),
    "short.q.answered": ("✔️ Question {q}: {text} → **{answer}** ({where} `{at}`)",
                         "✔️ 问题{q}：{text} → **{answer}**（{where} `{at}`）"),
    "short.q.unanswered": ("❔ Question {q}: {text} (not answered)", "❔ 问题{q}：{text}（未回答）"),

    # Long stories (long_stories.py, families/long_story)
    "long.label": ("[{engine}] #{n} {title}", "[{engine}] #{n} {title}"),  # a Telegram topic's name too
    "long.window": ("screen {sty}, window {window}", "screen {sty}的窗口{window}"),
    "long.no_window": ("a terminal outside screen", "screen 之外的终端"),
    "long.window_pys": ("pys window {window}", "pys 窗口{window}"),
    "long.resume_ask": ("{label}: nobody holds the session now, and your message waits. Resume it? "
                        "A resume loads the whole conversation again, which costs tokens. ▶ starts it as the "
                        "buddy's live session, with what you wrote.",
                        "{label}：现在没人持有这个会话，你的消息先等着。要恢复它吗？恢复会重新载入整段对话，耗 token。"
                        "▶ 会把它作为 buddy 持有的活会话启动，并带上你写的话。"),
    "long.made_live": ("Started from {where}, in `{cwd}`, as a live session the buddy holds.",
                       "从{where}发起，目录 `{cwd}`，作为 buddy 持有的活会话。"),
    "long.sent": ("📨 Sent to the live session.", "📨 已发给活会话。"),
    "long.live.started": ("🤖 The buddy holds the session now: one live process ({unit}), in `{cwd}`. What you "
                          "write here goes straight in. /status · /handover to take it to a terminal.",
                          "🤖 现在由 buddy 持有这个会话：一个活进程（{unit}），目录 `{cwd}`。你在这里写的话直接送进去。"
                          "/status · /handover 交给终端。"),
    "long.live.already": ("🤖 The buddy holds it already.", "🤖 已经由 buddy 持有。"),
    "long.live.terminal": ("👤 A terminal holds it (pid {pid}). /takeback waits for it to close.",
                           "👤 一个终端持有它（pid {pid}）。/takeback 会等它关闭。"),
    "long.live.held_elsewhere": ("⚠️ Another process has this session open (pid {pids}): two writers at once "
                                 "would fork it. Close that one first.",
                                 "⚠️ 另一个进程正打开着这个会话（pid {pids}）：两边同时写会让它分叉。请先关掉那个。"),
    "long.live.ended": ("💤 The live session ended (exit {rc}). Write here and a card asks before it resumes.",
                        "💤 活会话结束了（退出码 {rc}）。在这里写字，会先有卡片问你要不要恢复。"),
    "long.live.gap": ("⚠️ A terminal opened this session while the buddy held it: the buddy steps back now. "
                      "Next time use `buddy-long take {n}`: two writers at once can fork the session.",
                      "⚠️ buddy 持有时有终端打开了这个会话：buddy 现在退让。下次请用 `buddy-long take {n}`："
                      "两边同时写可能让会话分叉。"),
    "long.releasing": ("⏳ It finishes its turn, then it is yours. In a terminal: `buddy-long take {n}`",
                       "⏳ 它做完这一轮就交给你。在终端里：`buddy-long take {n}`"),
    "long.handed_over": ("📌 Handed over and kept for your terminal: `buddy-long take {n}` (or `cd {cwd} && "
                         "BUDDY_LONG=1 claude --resume {id}`). Messages here wait; /takeback brings it back.",
                         "📌 已交接，留给你的终端：`buddy-long take {n}`（或 `cd {cwd} && BUDDY_LONG=1 claude --resume {id}`）。"
                         "这里的消息先等着；/takeback 拿回来。"),
    "long.waits_terminal": ("📥 Kept: the session is handed over to your terminal (`buddy-long take {n}`). "
                            "/takeback brings it back here.",
                            "📥 已保留：会话已交给你的终端（`buddy-long take {n}`）。/takeback 拿回这里。"),
    "long.takeback.wait": ("⏳ A terminal holds it (pid {pid}). It comes back to the buddy when that session closes.",
                           "⏳ 一个终端持有它（pid {pid}）。那个会话关闭后就回到 buddy。"),
    "long.takeback.end_btn": ("⏹ End the terminal session now", "⏹ 现在结束终端里的会话"),
    "long.takeback.busy": ("Its turn is still running in the terminal; press again when it ends.",
                           "终端里这一轮还在运行；结束后再按一次。"),
    "long.takeback.ending": ("⏹ Ended the terminal's session between turns; the buddy takes it back.",
                             "⏹ 已在两轮之间结束终端里的会话；buddy 接回来。"),
    "long.takeback.soon": ("⏳ It finishes its turn first; then the buddy starts it again.",
                           "⏳ 它先做完这一轮，然后 buddy 重新启动它。"),
    "long.take.waiting": ("⏳ waiting for the buddy's turn to end…", "⏳ 等 buddy 这一轮结束……"),
    "long.take.timeout": ("The buddy's turn did not end in time; try again.", "buddy 这一轮没有按时结束；请再试。"),
    "long.status": ("{who} · {state} · {turns} turns · {waiting} waiting\nsession `{id}` in `{cwd}`\n{next}",
                    "{who} · {state} · {turns} 轮 · {waiting} 条在等\n会话 `{id}`，目录 `{cwd}`\n{next}"),
    "long.h.buddy": ("🤖 the buddy (live process, pid {pid})", "🤖 buddy（活进程，pid {pid}）"),
    "long.h.releasing": ("⏳ releasing to your terminal", "⏳ 正在交给你的终端"),
    "long.h.reserved": ("📌 reserved for your terminal", "📌 留给你的终端"),
    "long.h.terminal": ("👤 a terminal (pid {pid})", "👤 一个终端（pid {pid}）"),
    "long.h.nobody": ("💤 nobody", "💤 无人持有"),
    "long.next.buddy": ("/handover takes it to a terminal", "/handover 交给终端"),
    "long.next.releasing": ("wait for its turn to end, then `buddy-long take {n}`", "等这一轮结束，然后 `buddy-long take {n}`"),
    "long.next.reserved": ("`buddy-long take {n}` in a terminal, or /takeback", "在终端 `buddy-long take {n}`，或 /takeback"),
    "long.next.terminal": ("/takeback when you are done there", "那边做完后 /takeback"),
    "long.next.nobody": ("/takeback starts it here (tokens), or `buddy-long take {n}`",
                         "/takeback 在这里启动（耗 token），或 `buddy-long take {n}`"),
    "long.title.live": ("🤖 held by the buddy", "🤖 由 buddy 持有"),
    "long.title.handover": ("📌 hand-over", "📌 交接"),
    "long.resume_btn": ("▶ Resume", "▶ 恢复"),
    "long.later_btn": ("⏸ Not now", "⏸ 先不"),
    "long.kept": ("📥 Kept: it waits with the others for ▶ Resume on the card.",
                  "📥 已保留：和其他消息一起等卡片上的 ▶ 恢复。"),
    "long.joined": ("The session is held by {where}, in `{cwd}`. Its turns show here; what you write "
                    "here goes to it: typed in when it is idle, handed over when its turn ends.",
                    "这个会话由{where}持有，目录 `{cwd}`。它的每一轮都会显示在这里；你在这里写的话会交给它："
                    "空闲时直接输入，忙碌时在这一轮结束时交给它。"),
    "long.left": ("The terminal let the session go. Write here and a card asks first whether to resume "
                  "it (a resume loads the whole conversation again), or take it back: "
                  "`cd {cwd} && BUDDY_LONG=1 claude --resume {id}`",
                  "终端已放下这个会话。在这里写字，会先有一张卡片问你要不要恢复它（恢复会重新载入整段对话，耗 token），"
                  "或在终端接回：`cd {cwd} && BUDDY_LONG=1 claude --resume {id}`"),
    "long.made": ("Started from {where}, in `{cwd}`. Its first turn runs headless now.",
                  "从{where}发起，目录 `{cwd}`。第一轮正在无界面运行。"),
    "long.take": ("Take it to a terminal: `cd {cwd} && BUDDY_LONG=1 claude --resume {id}`",
                  "在终端接手：`cd {cwd} && BUDDY_LONG=1 claude --resume {id}`"),
    "long.waiting": ("⏳ {message}. Answer it in the terminal.", "⏳ {message}。请在终端里回答。"),
    "long.waiting.screen": ("⏳ {message}:\n\n```\n{screen}\n```\nReply with the number of your choice.",
                            "⏳ {message}：\n\n```\n{screen}\n```\n回复你要选的数字。"),
    "long.pressed": ("⌨ Pressed {key} in the session's dialog.", "⌨ 已在会话的对话框里按下 {key}。"),
    "long.waits": ("📥 It waits on a dialog: reply with a number to answer it. This waits until then.",
                   "📥 它在等一个对话框：回复数字来回答。这条消息先排队。"),
    "long.handed": ("The owner wrote from {where} while you were working: {text}",
                    "你工作时，主人从{where}写道：{text}"),
    "long.typed": ("⌨ Typed into the session's terminal.", "⌨ 已输入到会话的终端。"),
    "bot.adopted": ("↩️ This answer finished while the buddy restarted. You asked: {prompt}",
                    "↩️ 这个回答是在 buddy 重启期间完成的。你问的是：{prompt}"),
    "long.queued": ("📥 It is working: it gets this when its turn ends.", "📥 它正在工作：这一轮结束时会交给它。"),
    "long.no_text": ("(no text)", "（无文字）"),
    "long.no_cwd": ("This session ran in `{cwd}`, which is gone, and a session is resumed from its "
                    "own folder. Start a new one: /newlong <what to do>",
                    "这个会话运行在 `{cwd}`，但该目录已不存在，而会话只能从原目录恢复。请新开一个：/newlong <要做的事>"),
    "long.failed": ("⚠️ The headless turn did not start: {e}", "⚠️ 无界面的一轮没能启动：{e}"),
    "long.no_such": ("No long story #{n}.", "没有长篇故事 #{n}。"),
    "long.no_engine": ("Nobody holds this session, and no engine is here to carry it on.",
                       "没有人持有这个会话，这里也没有引擎可以继续它。"),
    "long.none": ("No long stories yet. Start one in a terminal: `BUDDY_LONG=\"<name>\" claude`, "
                  "or here: /newlong <what to do>",
                  "还没有长篇故事。在终端发起：`BUDDY_LONG=\"<名字>\" claude`，或在这里：/newlong <要做的事>"),
    "long.board_line": ("{label} — {who}, {turns} turns, `{cwd}`", "{label} — {who}，{turns}轮，`{cwd}`"),
    "long.held": ("held by a terminal", "由终端持有"),
    "long.away": ("nobody holds it", "无人持有"),
    "long.help": ("long stories, one thread per agent session: the list (/long help: how)",
                  "长篇故事，每个智能体会话一个线程：列表（/long help：怎么用）"),
    "long.help_new": ("start a long story: a card asks the agent, the folder and the task, or all in one line",
                      "发起一个长篇故事：卡片问智能体、目录和要做的事，或一行写完"),
    "long.start_hint": ("Start one: /newlong (a card asks the rest), or /newlong <what to do>",
                        "新建：/newlong（卡片问其余的），或 /newlong <要做的事>"),
    "long.carded": ("A card asks the agent, the folder, then the task.", "卡片会依次问智能体、目录，再请你回复要做的事。"),
    "long.usage": (
        "**Long stories**: one agent session (Claude Code or Codex) mirrored to a thread here, so "
        "you can follow it and talk to it from anywhere. The session's own record is the truth; the "
        "terminal or the phone drives it, one at a time.\n\n"
        "**Start one from here**\n"
        "- `/newlong`: a card asks the agent, the folder, then the task (reply to its question). "
        "📂 Other folder… takes any folder as a reply: a name under ~/projects or a path; a miss lists "
        "what is there (a fragment works as a search)\n"
        "- `/newlong [claude|codex] [folder] <task>`: the same in one line (a folder that isn't "
        "there starts nothing)\n"
        "- `/newlong [claude|codex] [folder] --short 12:test <task>`: the session works for step test of "
        "short story #12, as its child story (it gets the number in `$BUDDY_SHORT`)\n"
        "- `/newlong --open [claude|codex] [folder] <task>`: 🔓 open, like a session in your terminal "
        "(your ssh agent, `gh`, every folder; the buddy's own keys stay hidden). Without it: 🔒 the gate's "
        "sandbox (only its folder, no keys). Its tools: `[long.open]` in buddy.toml over `[long]`\n\n"
        "**Start one in a terminal** (inside screen, so the phone can type into it)\n"
        "- Claude Code: `BUDDY_LONG=\"<name>\" claude`\n"
        "- Codex: `BUDDY_LONG=\"<name>\" buddy-long codex`\n"
        "`BUDDY_LONG` is an environment variable for that one command: it marks the session as a "
        "long story and names it (`1` uses the folder's name). Sessions started without it are left alone.\n\n"
        "**In its thread**\n"
        "- What you write goes to the session: typed in when it is idle, handed over when its turn ends "
        "(Codex: `codex queue`). With no terminal holding it, it runs as a turn of its own, "
        "with the tools `[long]` in buddy.toml gives it (it may edit its own folder; pushing stays with you).\n"
        "- A permission it waits for: reply with the number (Claude Code), or press Allow / Deny (Codex).\n"
        "- The buddy's own commands (`/help`, `/long`, …) are answered here, by the buddy; the session never sees them.\n"
        "- To take it to a terminal: the command in the thread (`claude --resume <id>`, `buddy-long codex resume <id>`).\n\n"
        "`/long`: every long story · the workbench: the 长篇故事 / long-story lane",
        "**长篇故事**：把一个智能体会话（Claude Code 或 Codex）镜像到这里的一个线程，让你随时随地关注它、和它对话。"
        "会话自己的记录是唯一的事实来源；终端或手机驱动它，同一时间只有一方。\n\n"
        "**在这里发起**\n"
        "- `/newlong`：卡片依次问智能体、目录，再请你回复要做的事。"
        "📂 其他目录… 可以回复任意目录：~/projects 下的名字或路径；找不到时列出那里有什么（输入片段就能搜索）\n"
        "- `/newlong [claude|codex] [目录] <要做的事>`：一行写完（目录不存在就不发起）\n"
        "- `/newlong [claude|codex] [目录] --short 12:test <要做的事>`：会话作为子故事负责短篇故事 #12 的 test 步骤"
        "（编号在 `$BUDDY_SHORT` 里）\n"
        "- `/newlong --open [claude|codex] [目录] <要做的事>`：🔓 开放，像你在终端里开的会话"
        "（你的 ssh agent、`gh`、所有目录；buddy 自己的密钥仍然看不到）。不加：🔒 闸门的沙盒（只有它的目录，没有密钥）。"
        "它的工具：buddy.toml 里 `[long.open]` 覆盖 `[long]`\n\n"
        "**在终端发起**（在 screen 里，手机才能往里输入）\n"
        "- Claude Code：`BUDDY_LONG=\"<名字>\" claude`\n"
        "- Codex：`BUDDY_LONG=\"<名字>\" buddy-long codex`\n"
        "`BUDDY_LONG` 是只对这一条命令生效的环境变量：它把会话标记为长篇故事并给它起名（写 `1` 就用目录名）。"
        "没带它启动的会话不受影响。\n\n"
        "**在它的线程里**\n"
        "- 你写的话会交给会话：空闲时直接输入，忙碌时在这一轮结束时交给它（Codex 用 `codex queue`）。"
        "没有终端持有时，它会自己跑一轮，"
        "用 buddy.toml 里 `[long]` 给的工具（可以改自己目录里的代码；推送仍由你来）。\n"
        "- 它在等权限时：回复数字（Claude Code），或点允许 / 拒绝（Codex）。\n"
        "- buddy 自己的命令（`/help`、`/long` 等）由 buddy 在这里直接回答，不交给会话。\n"
        "- 想在终端接手：用线程里给出的命令（`claude --resume <id>`、`buddy-long codex resume <id>`）。\n\n"
        "`/long`：全部长篇故事 · 工作台：长篇故事那一行"),
    "long.about": ("one agent session, mirrored to a thread; the terminal or the phone drives it",
                   "一个智能体会话，镜像到一个线程；由终端或手机驱动"),
    "long.title.terminal": ("▶ held by a terminal", "▶ 由终端持有"),
    "long.title.turn": ("turn {n}", "第{n}轮"),
    "long.title.headless": ("turn {n}, headless", "第{n}轮（无界面）"),
    "long.title.waiting": ("waiting for you", "等你回应"),
    "long.title.away": ("⏸ nobody holds it", "⏸ 无人持有"),
    "long.title.new": ("▶ started", "▶ 已发起"),
    "long.title.start": ("📟 a new long story", "📟 新的长篇故事"),
    "long.pick_engine": ("Which agent?", "用哪个智能体？"),
    "long.pick_folder": ("In which folder?", "在哪个目录？"),
    "long.other_folder": ("📂 Other folder…", "📂 其他目录…"),
    "long.type_path": ("✏️ Type a path", "✏️ 输入路径"),
    "long.ask_path": ("✏️ Which folder? Reply to this message with its path: ~/… or /…, or a name under "
                      "~/projects (reply “cancel” to start nothing).",
                      "✏️ 用哪个目录？请回复这条消息，写上路径：~/… 或 /…，或 ~/projects 下的名字（回复“取消”则不发起）。"),
    "long.ask_folder": ("📂 `{near}` holds: {subs}\n\nWhich folder? Reply to this message with a name under "
                        "~/projects, or a full path (~/… or /…); end it with / to see what is inside "
                        "(reply “cancel” to start nothing).",
                        "📂 `{near}` 里有：{subs}\n\n用哪个目录？请回复这条消息：~/projects 下的名字，或完整路径（~/… 或 /…）；"
                        "以 / 结尾可以看看里面有什么（回复“取消”则不发起）。"),
    "long.no_folder": ("✖ No folder `{typed}`.", "✖ 没有目录 `{typed}`。"),
    "long.no_such_folder": ("✖ No folder `{folder}`: nothing started. Check the path, or leave it out to "
                            "use the workspace.",
                            "✖ 没有目录 `{folder}`，没有发起。请检查路径，或者不写路径，就用默认工作区。"),
    "long.pick_mode": ("🔒 Sandboxed: only its folder is writable; no ssh, gh or keyring.\n"
                       "🔓 Open: like a session in your terminal (your ssh agent, gh, every folder); "
                       "the buddy's own keys stay hidden. Which one?",
                       "🔒 沙盒：只能写它自己的目录；没有 ssh、gh、钥匙环。\n"
                       "🔓 开放：像你在终端开的会话（你的 ssh agent、gh、所有目录）；buddy 自己的密钥仍然看不到。选哪个？"),
    "long.mode.sandbox_btn": ("🔒 Sandboxed", "🔒 沙盒"),
    "long.mode.open_btn": ("🔓 Open", "🔓 开放"),
    "long.mode.open_short": ("(🔓 open)", "（🔓 开放）"),
    "long.mode.open": ("🔓 Open, your choice: your ssh agent, gh and every folder, like your terminal; "
                       "the buddy's own keys stay hidden. Its later turns run open too.",
                       "🔓 开放，你选的：你的 ssh agent、gh 和所有目录，像你的终端；buddy 自己的密钥仍然看不到。之后的每一轮也是开放的。"),
    "long.ask_task": ("{engine} in `{folder}`: what should it do? Answer by replying to this message "
                      "(reply “cancel” to start nothing).",
                      "{engine}，目录 `{folder}`：要它做什么？请回复这条消息（回复“取消”则不发起）。"),
    "long.cancelled": ("✖ Cancelled: no long story started.", "✖ 已取消，没有发起长篇故事。"),
    "long.take.codex": ("Take it to a terminal: `cd {cwd} && buddy-long codex resume {id}`",
                        "在终端接手：`cd {cwd} && buddy-long codex resume {id}`"),
    "long.sent_codex": ("📨 Sent to the Codex session; its answer comes here.", "📨 已发给 Codex 会话，回答会出现在这里。"),
    "long.perm": ("{label} asks to use **{tool}**:\n\n{what}", "{label} 请求使用 **{tool}**：\n\n{what}"),
    "long.allow": ("✅ Allow", "✅ 允许"),
    "long.deny": ("⛔ Deny", "⛔ 拒绝"),

    # The scheduler (scheduler.py run_now)
    "sched.none": ("no {kind} {name}", "没有{kind} {name}"),
    "sched.paused": ("paused: /resume first", "已暂停：请先 /resume"),
    "sched.running": ("it is running already", "它已经在运行了"),
    "sched.started": ("started", "已启动"),

    # Duty: code review
    "cr.help.update": ("check my PRs/MRs now and show the table", "现在检查我的 PR/MR 并显示表格"),
    "cr.help.watch": ("<PR or MR url>: watch this one too", "<PR 或 MR 链接>：把这个也加入关注"),
    "cr.help.unwatch": ("<url>: stop watching it", "<链接>：不再关注它"),
    "cr.note": ("/update shows every PR", "/update 显示全部 PR"),
    "cr.offer.rebase": ("**[{short}]({url})** {what}. Draft a rebase in a scratch worktree? Nothing is "
                        "pushed without another yes.",
                        "**[{short}]({url})** {what}。要在临时工作树里起草一次变基吗？没有你再次同意，不会推送任何内容。"),
    "cr.title.rebase": ("{short}: rebase?", "{short}：变基？"),
    "cr.opt.draft": ("↻ Draft a rebase", "↻ 起草变基"),
    "cr.first": ("Now watching {n} open PRs/MRs. I'll tell you when they change.",
                 "现在关注 {n} 个未关闭的 PR/MR，有变化时我会告诉你。"),
    "cr.fixed": ("✅ fixed: {what} answers again", "✅ 已恢复：{what} 又能访问了"),
    "cr.not_watched": ("`{key}` is not watched any more.", "`{key}` 已不在关注之列。"),
    "cr.no_clone": ("{short}: no local clone of `{repo}` under {roots}, so no draft.",
                    "{short}：在 {roots} 下没有 `{repo}` 的本地克隆，无法起草。"),
    "cr.no_draft": ("{short}: no draft to {verb}.", "{short}：没有可{verb}的草稿。"),
    "cr.verb.push": ("push", "推送"),
    "cr.verb.drop": ("drop", "丢弃"),
    "cr.pushed": ("{short}: ⬆ force-pushed `{new}` to `{url}` (the PR updates).",
                  "{short}：⬆ 已把 `{new}` 强制推送到 `{url}`（PR 随之更新）。"),
    "cr.dropped": ("{short}: draft dropped, nothing pushed.", "{short}：草稿已丢弃，没有推送任何内容。"),
    "cr.current": ("{short}: already on top of `{base}`, nothing to rebase.",
                   "{short}：已经在 `{base}` 之上，无需变基。"),
    "cr.left": ("{short}: the rebase stopped at the conflict, for you to resolve in `{wt}` ({files}). "
                "Then `git rebase --continue` there.",
                "{short}：变基停在冲突处，请在 `{wt}` 中解决（{files}），然后在那里运行 `git rebase --continue`。"),
    "cr.conflict": ("{short}: rebasing onto `{base}` conflicts in {files}. Which way? Git decides each "
                    "conflicting hunk the way you pick; nothing is written by hand.",
                    "{short}：变基到 `{base}` 时，{files} 有冲突。保留哪一边？Git 会按你的选择处理每个冲突块，不手写任何代码。"),
    "cr.title.conflict": ("{short}: conflict", "{short}：冲突"),
    "cr.opt.mine": ("Keep mine where they conflict", "冲突处保留我的"),
    "cr.opt.upstream": ("Keep upstream's", "保留上游的"),
    "cr.opt.myself": ("Leave it to me, in a worktree", "交给我，在工作树里处理"),
    "cr.guessed": (" 🔎 (the fork's path is guessed)", " 🔎（fork 的路径是推测的）"),
    "cr.rebased": ("{short}: rebased onto `{base}` in `{wt}`: {n} commit(s), {stat}.\n",
                   "{short}：已在 `{wt}` 中变基到 `{base}`：{n} 个提交，{stat}。\n"),
    "cr.push_offer": ("\n\nForce-push it to `{repo}`{guessed}? Its reviewers see the update.",
                      "\n\n要强制推送到 `{repo}`{guessed} 吗？它的评审者会看到这次更新。"),
    "cr.title.push": ("{short}: push?", "{short}：推送？"),
    "cr.opt.push": ("⬆ Push it", "⬆ 推送"),
    "cr.opt.drop": ("Drop the draft", "丢弃草稿"),
    "cr.push_yourself": ("\n\n`{repo}` is not in push_repos, so push it yourself{guessed}:\n```\n{cmd}\n```",
                         "\n\n`{repo}` 不在 push_repos 中，请自己推送{guessed}：\n```\n{cmd}\n```"),
    "cr.title.ready": ("{short}: draft ready", "{short}：草稿已就绪"),
    "cr.no_changes": ("No changes since the last check.\n\n", "自上次检查以来没有变化。\n\n"),
    "cr.give_link": ("Give me a PR or MR link, e.g. `/watch https://github.com/owner/repo/pull/12`",
                     "请给我一个 PR 或 MR 链接，例如 `/watch https://github.com/owner/repo/pull/12`"),
    "cr.watching": ("Watching `{key}`. `/update` shows it now.", "已关注 `{key}`，现在用 `/update` 就能看到。"),
    "cr.unwatched": ("Stopped watching `{key}`.", "已不再关注 `{key}`。"),
    "cr.ev.new": ("👀 new, now watching", "👀 新的，开始关注"),
    "cr.ev.merged": ("✅ merged", "✅ 已合并"),
    "cr.ev.closed": ("✖️ closed", "✖️ 已关闭"),
    "cr.ev.reopened": ("↺ reopened", "↺ 已重新打开"),
    "cr.ev.ci_failed": ("CI ✗ failed", "CI ✗ 失败"),
    "cr.ev.ci_passed": ("CI ✓ passed again", "CI ✓ 重新通过"),
    "cr.ev.approved": ("👍 approved", "👍 已批准"),
    "cr.ev.changes": ("✋ changes requested", "✋ 要求修改"),
    "cr.ev.conflict": ("⚠️ merge conflict", "⚠️ 合并冲突"),
    "cr.ev.rebase": ("↻ needs a rebase", "↻ 需要变基"),
    "cr.ev.comment": ("💬 **{by}**{on}: {text} [↗]({url})", "💬 **{by}**{on}：{text} [↗]({url})"),
    "cr.ev.on": (" on `{path}`", "（`{path}`）"),
    "cr.ev.earlier.one": ("💬 {n} earlier new comment", "💬 另有 {n} 条更早的新评论"),
    "cr.ev.earlier.many": ("💬 {n} earlier new comments", "💬 另有 {n} 条更早的新评论"),
    "cr.no_text": ("(no text)", "（无文字）"),
    "cr.none": ("No open PRs/MRs to watch.", "没有要关注的未关闭 PR/MR。"),
    "cr.head": ("| PR | CI | status | title |", "| PR | CI | 状态 | 标题 |"),
    "cr.merge.conflict": ("⚠️conflict", "⚠️冲突"),
    "cr.merge.rebase": ("↻rebase", "↻需变基"),
    "cr.foot": ("\n\n{n} open, {k} need you (✗ CI, ✋ changes, conflict or rebase).",
                "\n\n{n} 个未关闭，{k} 个需要你处理（✗ CI、✋ 要求修改、冲突或需变基）。"),

    # Duty: threads
    "th.help": ("the GitHub threads I'm in: latest first, then the ones I talk in most",
                "我参与的 GitHub 讨论：最新的在前，然后是我发言最多的"),
    "th.note": ("/threads shows them all", "/threads 显示全部"),
    "th.first": ("Now following {n} GitHub threads; {k} are your turn.\n\n",
                 "现在跟踪 {n} 个 GitHub 讨论，其中 {k} 个轮到你。\n\n"),
    "th.digest": ("☀️ {k} threads are your turn.", "☀️ 有 {k} 个讨论轮到你。"),
    "th.nothing": ("Nothing turned to you since the last check.\n\n", "自上次检查以来，没有新轮到你的讨论。\n\n"),
    "th.your_turn": ("- ↩ your turn: **[{short}]({url})** {title} ({last}, {at})",
                     "- ↩ 轮到你：**[{short}]({url})** {title}（{last}，{at}）"),
    "th.nobody": ("nobody yet", "还没有人"),
    "th.none": ("No open GitHub threads in the window.", "时间窗口内没有未关闭的 GitHub 讨论。"),
    "th.latest": ("**Latest** · active in the last {days} days, newest first",
                  "**最新** · 最近 {days} 天内有动静，最新的在前"),
    "th.most": ("**Most talked in** · the rest, by how often I spoke", "**发言最多** · 其余的，按我发言的次数排序"),
    "th.head": ("| turn | thread | 💬 | last | title |", "| 轮到 | 讨论 | 💬 | 最近 | 标题 |"),
    "th.turn.you": ("you", "你"),
    "th.turn.new": ("new", "新"),
    "th.turn.them": ("them", "对方"),
    "th.kind.PR": ("PR", "PR"),
    "th.kind.issue": ("issue", "议题"),
    "th.kind.discussion": ("disc.", "讨论"),
    "th.foot": ("\n\n{you} your turn · {new} waiting for a first answer · {them} waiting for them",
                "\n\n{you} 个轮到你 · {new} 个等待首次回复 · {them} 个等待对方"),
    "th.changed": (" · * changed since the last check", " · * 自上次检查后有变化"),

    # Family: upstream MR
    "um.help": ("upstream MRs as stories: whose turn each one is (/stories all: every row)",
                "把上游 MR 当作故事：每个轮到谁（/stories all：所有行）"),
    "um.none": ("No stories: the code-review duty has no snapshot yet and the ledger has no open entry.",
                "没有故事：代码评审职责还没有快照，台账里也没有未完成的条目。"),
    "um.head": ("**{n} upstream MR stories** · your turn **{you}** · executor {executor} · upstream {upstream}",
                "**{n} 个上游 MR 故事** · 轮到你 **{you}** · 执行者 {executor} · 上游 {upstream}"),
    "um.table": ("| Turn | Story | Why |", "| 轮到 | 故事 | 原因 |"),
    "um.turn.you": ("you", "你"),
    "um.turn.executor": ("executor", "执行者"),
    "um.turn.upstream": ("upstream", "上游"),
    "um.waiting": ("Waiting on upstream: {list}", "等待上游：{list}"),
    "um.why.changes": ("changes requested", "要求修改"),
    "um.why.spoke": ("{last} spoke last, {at}", "{last} 最后发言，{at}"),
    "um.why.commented": ("{by} commented {at}; your reply not known", "{by} 于 {at} 评论，不知道你是否已回复"),
    "um.why.conflict": ("conflict: draft a rebase?", "冲突：要起草变基吗？"),
    "um.why.rebase": ("rebase: draft a rebase?", "需要变基：要起草变基吗？"),
    "um.why.ci": ("CI failed: find out why", "CI 失败：查明原因"),
    "um.why.approved": ("approved, waiting for the merge", "已批准，等待合并"),
    "um.why.review": ("waiting for review (CI {ci})", "等待评审（CI {ci}）"),
    "um.why.ledger": ("ledger since {date}: {title}", "自 {date} 起在台账中：{title}"),

    # Family: monthly report
    "mr.help": ("this month so far (/report 2026-09: a past month; add all: every row)",
                "本月至今（/report 2026-09：过去某个月；加上 all：所有行）"),
    "mr.bad_month": ("Not a month: {month}. Use YYYY-MM, e.g. /report 2026-09.",
                     "不是月份：{month}。请用 YYYY-MM，例如 /report 2026-09。"),
    "mr.to_date": (" (to {day})", "（截至 {day}）"),
    "mr.head": ("**{month}{to_date}** · upstream: **{merged} merged**, {opened} opened, {closed} closed, "
                "{open} open · your turn on **{yours}**",
                "**{month}{to_date}** · 上游：**合并 {merged} 个**，新开 {opened} 个，关闭 {closed} 个，"
                "未关闭 {open} 个 · 轮到你的 **{yours}** 个"),
    "mr.merged": ("**Merged** · ", "**已合并** · "),
    "mr.everything": ("everything in the month", "本月全部"),
    "mr.still_open": ("still open", "仍未关闭"),
    "mr.timeline": ("**Timeline**, {what} (█ open · M merged · x closed · > open now)",
                    "**时间线**，{what}（█ 未关闭 · M 已合并 · x 已关闭 · > 至今未关闭）"),
    "mr.your_turn": ("**Your turn**", "**轮到你**"),
    "mr.stale": ("**Asked: ping, wait or drop?** Open {days}+ days, no answer from upstream:",
                 "**请你定：催一下、再等等，还是放弃？** 已开 {days} 天以上，上游没有回应："),
    "mr.days": ("{n} days", "{n} 天"),
    "mr.buddy": ("**The buddy** · {runs} runs ({failed} failed) · ${usd} at list price · "
                 "{answered}/{asks} questions answered",
                 "**buddy 自身** · {runs} 次运行（{failed} 次失败）· 按标价 ${usd} · 回答了 {answered}/{asks} 个问题"),
    "mr.since": (" · records since {since}", " · 记录始于 {since}"),
    "mr.own": ("Not counted above: {n} PRs in my own repositories.", "未计入上面：我自己仓库里的 {n} 个 PR。"),
    "mr.gh_cap": ("GitHub: {n} PRs, only the first 100 are counted", "GitHub：共 {n} 个 PR，只统计了前 100 个"),
    "mr.gl_none": ("GitLab {project}: no answer", "GitLab {project}：无响应"),

    # Run records' SOPs (runs.SOPS), as the page draws them: "sop." + the English label. The
    # page gets them as "label." + the English label and translates the English SOPS itself.
    "sop.on schedule, or /update": ("on schedule, or /update", "按计划，或 /update"),
    "sop.check what changed": ("check what changed", "检查有何变化"),
    "sop.an answer asked for it": ("an answer asked for it", "某个回答要求这样做"),
    "sop.do what the answer asked": ("do what the answer asked", "执行回答所要求的"),
    "sop.when it has a question": ("when it has a question", "有问题要问时"),
    "sop.ask you, with buttons": ("ask you, with buttons", "用按钮问你"),
    "sop.something to say": ("something to say", "有话要说"),
    "sop.post it to every linked chat": ("post it to every linked chat", "发到每个关联的聊天"),
    "sop.nothing new": ("nothing new", "没有新内容"),
    "sop.say nothing": ("say nothing", "什么也不说"),
    "sop.when you answer": ("when you answer", "当你回答时"),
    "sop.your answer, from any app": ("your answer, from any app", "你的回答，来自任一应用"),
    "sop.run the agent on the job's prompt": ("run the agent on the job's prompt", "用任务的提示词运行智能体"),
    "sop.post the answer; a failure asks ↻ retry?": ("post the answer; a failure asks ↻ retry?",
                                                     "发出答案；失败时询问 ↻ 重试？"),
    "sop.render the scheduled message": ("render the scheduled message", "生成计划中的消息"),
    "sop.your message, to the chat's agent session": ("your message, to the chat's agent session",
                                                      "你的消息，交给该聊天的智能体会话"),

    # The workbench page (static/index.html): {fields} are filled in by the page
    "page.title": ("brahman workbench", "brahman 工作台"),
    # The corner (top right): what this is, at the top. Only positive words: no "replace".
    "page.motto.humble": ("We are making new life: we stay humble before nature, our God.",
                          "我们在创造新的生命，须对自然神常怀谦卑。"),
    "page.motto.main": ("Amplify people. Evolve together.", "把人放大，一起进化。"),
    "page.motto.r1": ("It feeds on the entropy of real work, and grows order and life.",
                      "它以真实工作的熵为食，长出秩序与生命。"),
    "page.motto.r2": ("Part digital + part human = a humanity that keeps evolving.",
                      "数字的一部分 + 人的一部分 = 不断进化的新人类。"),
    "page.meter": ("last {days} days ({from} on): {n} · digital {d}% · human {h}%",
                   "最近 {days} 天（{from} 起）：{n} · 数字 {d}% · 人 {h}%"),
    "page.meter_all": ("all ({from} on): {n} · digital {d}% · human {h}%",
                       "全部（{from} 起）：{n} · 数字 {d}% · 人 {h}%"),
    "page.meter_title_all": ("All of it, since {from}: {dn} things agents did and {hn} things you decided or said.",
                             "全部（{from} 起）：智能体做了 {dn} 件事，你决定或说了 {hn} 件事。"),
    "page.meter_title": ("The last {days} days, counted from the records: {dn} things agents did "
                         "(runs that worked, story marks and turns) and {hn} things you decided or "
                         "said (messages, answers, replies, Allow / Deny).",
                         "最近 {days} 天，按记录计数：智能体做了 {dn} 件事（有结果的运行、故事的进度和轮次），"
                         "你决定或说了 {hn} 件事（消息、回答、回复、允许 / 拒绝）。"),
    "page.film_title": ("The film: click to watch it large", "影片：点击放大观看"),
    "page.film_close": ("close (Esc)", "关闭（Esc）"),
    "page.motto.what": ("What", "做什么"),
    "page.motto.why": ("Why", "为什么"),
    "page.motto.what.top": ("An engineering bench: agents do the work, you make the calls.",
                            "一张工程工作台：智能体做事，你做决定。"),
    "page.motto.what.main": ("A workbench that evolves with you.", "与你一起进化的工作台。"),
    "page.motto.what.r1": ("It watches pipelines, PRs and sessions, and asks you on your phone when it matters.",
                           "流水线、PR、会话都替你盯着，需要你时就在手机上问你。"),
    "page.motto.what.r2": ("Anywhere, any time: Lark, Telegram and the web show the same card.",
                           "随时随地：Lark、Telegram、网页，同一张卡片。"),
    "page.tab.bench": ("Workbench", "工作台"),
    "page.tab.chat": ("Chat", "聊天"),
    "page.tab.setup": ('Setup', '设置'),
    "page.setup.ping": ('Ping', 'Ping 测试'),
    "page.setup.ping_sub": ("the buddy posts a 🏓 ping card into a chat through its own app: typing pong proves messages come in, its Pong button proves the card callback (on Lark and Feishu: 卡片回传交互)", "助手通过各自的应用往聊天里发 🏓 ping 卡片：回复 pong 证明能收到消息，按卡片上的 Pong 按钮证明卡片回传交互可用"),
    "page.setup.chat": ('Chat', '聊天'),
    "page.setup.app": ('App', '应用'),
    "page.setup.sent": ('Sent', '已发送'),
    "page.setup.pong": ('Pong', 'Pong'),
    "page.setup.send_ping": ('Send ping', '发送 ping'),
    "page.setup.waiting_pong": ('waiting: answer pong in that chat', '等待中：在那个聊天里回复 pong'),
    "page.setup.no_chats": ('No linked chats yet (buddy.toml [me] chats): pair one below, then restart.', '还没有关联的聊天（buddy.toml [me] chats）：在下面配对一个，然后重启。'),
    "page.setup.refused": ('Wrote, but not paired', '发过消息但未配对'),
    "page.setup.no_refused": ('Nobody refused.', '没有被拒绝的人。'),
    "page.setup.times": ('{n}×', '{n} 次'),
    "page.setup.pair": ('Pair', '配对'),
    "page.setup.pinging": ('sending a ping to {chat} …', '正在向 {chat} 发送 ping…'),
    "page.setup.pinged": ('ping {code} sent: answer pong in that chat', 'ping {code} 已发送：在那个聊天里回复 pong'),
    "page.setup.pair_helper": ('Pair: who wrote to the bot', '配对：给机器人发过消息的人'),
    "page.setup.pair_how": ('write anything to the bot in the new app (Feishu, Lark, Telegram); it shows up here; Pair writes the id and its chat, then restart the buddy', '在新应用（飞书、Lark、Telegram）里给机器人随便发一句话，它会出现在这里；点“配对”写入 id 和它的聊天，然后重启助手'),
    "page.setup.wrote": ("{sender} wrote to the bot on {app} ({n}×, last {last}) and isn't paired", '{sender} 在 {app} 上给机器人发了消息（{n} 次，最近 {last}），还没配对'),
    "page.setup.pair_restart": ('Pair and restart', '配对并重启'),
    "page.setup.pair_only": ('Pair only', '只配对'),
    "page.setup.back": ('the buddy is back: paired', '助手已重新运行：配对完成'),
    "page.setup.button": ('Button', '按钮'),
    "page.setup.press_pong": ('press 🏓 Pong on the card', '在卡片上按 🏓 Pong'),
    "page.setup.ignore": ('Ignore', '忽略'),
    "page.setup.unlinked": ("{sender} writes from {chat} on {app}, which isn't linked: cards, asks and pings don't go there", '{sender} 在 {app} 上从 {chat} 发消息，但这个聊天没有关联：卡片、提问和 ping 不会发到那里'),
    "page.setup.link_restart": ('Link and restart', '关联并重启'),
    "page.setup.link_only": ('Link only', '只关联'),
    "page.setup.machine": ('machine {fp}: this side\'s name is tied to it', '机器 {fp}：本机的名字与它绑定'),
    "page.setup.doctor": ('Doctor', '体检'),
    "page.setup.config": ('Config', '配置'),
    "page.setup.peers": ('Peers', '对等节点'),
    "page.setup.log": ('Log', '日志'),
    "page.setup.quick": ('Quick checks', '快速检查'),
    "page.setup.full": ('Every check', '全部检查'),
    "page.setup.full_sub": ('every check calls the chat apps and the peers; buddy doctor --json is the same for scripts', '全部检查会调用聊天应用和对等节点；脚本用 buddy doctor --json，结果相同'),
    "page.setup.checking": ('checking …', '检查中…'),
    "page.setup.checking_all": ('every check: calling the chat apps and the peers …', '全部检查：正在调用聊天应用和对等节点…'),
    "page.setup.ran_quick": ('the quick checks, without calling the chat apps; what this running buddy has', '快速检查，不调用聊天应用；检查的是正在运行的助手的配置'),
    "page.setup.ran_all": ('every check; what this running buddy has (a change written here counts after a restart)', '全部检查；检查的是正在运行的助手的配置（这里写入的改动重启后才生效）'),
    "page.setup.bad": ("{n} ❌ to fix: each says how", "{n} 个 ❌ 需要修复：每项都写了怎么修"),
    "page.setup.no_bad": ('no ❌', '没有 ❌'),
    "page.setup.setting": ('Setting', '设置项'),
    "page.setup.now": ('Now', '当前'),
    "page.setup.new": ('New value', '新值'),
    "page.setup.write_config": ('Write buddy.toml and .env', '写入 buddy.toml 和 .env'),
    "page.setup.config_sub": ('secrets never come to this page: type a new one, or - to remove it; empty keeps it', '密钥从不发到这个页面：输入新的，或输入 - 删除；留空则保留'),
    "page.setup.not_set_up": ('not set up: run buddy init first', '尚未设置：请先运行 buddy init'),
    "page.setup.nothing": ('nothing changed', '没有改动'),
    "page.setup.f.language": ('language', '语言'),
    "page.setup.f.workspace": ('workspace', '工作目录'),
    "page.setup.f.telegram": ('Telegram', 'Telegram'),
    "page.setup.f.lark": ('Lark', 'Lark'),
    "page.setup.f.feishu": ('Feishu', '飞书'),
    "page.setup.f.allowed": ('paired people', '已配对的人'),
    "page.setup.f.chats": ('linked chats', '关联的聊天'),
    "page.setup.f.port": ('web port', '网页端口'),
    "page.setup.ph_secret": ('new secret (hidden)', '新密钥（隐藏）'),
    "page.setup.ph_list": ('comma separated', '用逗号分隔'),
    "page.setup.ph_appid": ('App ID', 'App ID'),
    "page.setup.peer_warn": ('⚠️ One shared secret, plain HTTP: a LAN you own, a VPN or an SSH tunnel only, never the open internet.', '⚠️ 一个共享密钥，明文 HTTP：只在你自己的局域网、VPN 或 SSH 隧道里用，绝不放到公网。'),
    "page.setup.side": ('This side', '本机'),
    "page.setup.bind": ('serve on', '监听地址'),
    "page.setup.port": ('port', '端口'),
    "page.setup.secret_set": ('BRAHMAN_PEER_SECRET set', 'BRAHMAN_PEER_SECRET 已设置'),
    "page.setup.secret_unset": ("BRAHMAN_PEER_SECRET not set: put it in the buddy's .env, the same on both sides, then restart", 'BRAHMAN_PEER_SECRET 未设置：写进助手的 .env，两边相同，然后重启'),
    "page.setup.peer": ('Peer', '节点'),
    "page.setup.address": ('Address', '地址'),
    "page.setup.note": ('Note', '备注'),
    "page.setup.check": ('Check', '检查'),
    "page.setup.delete": ('Delete', '删除'),
    "page.setup.add": ('Add a peer', '添加节点'),
    "page.setup.write_peers": ('Write peers.toml', '写入 peers.toml'),
    "page.setup.no_peers": ('No peers yet.', '还没有节点。'),
    "page.setup.f.notify_feishu": ('Feishu on its own', '飞书主动发送'),
    "page.setup.f.notify_lark": ('Lark on its own', 'Lark 主动发送'),
    "page.setup.notify_off": ('off: nothing on its own; it answers you', '关：不主动发；只回答你'),
    "page.setup.notify_ends": ('ends: a story\'s start and end only', '两端：只发故事的开始和结束'),
    "page.setup.notify_all": ('all: every card change, posts, mirrors', '全部：每次卡片变化、帖子、镜像'),
    "page.setup.desc": ('What this machine is', '这台机器是什么'),
    "page.setup.desc_sub": ('seen by your peers (sent in the ping): no secrets', '对端可见（随 ping 发送）：不要写机密'),
    "page.setup.allow": ('Who may connect', '谁可以连接'),
    "page.setup.allow_local": ('local: this machine and SSH tunnels', 'local：本机和 SSH 隧道'),
    "page.setup.allow_private": ('private: also LAN and VPN', 'private：另加局域网和 VPN'),
    "page.setup.allow_public": ('public: anyone (plain HTTP)', 'public：任何人（明文 HTTP）'),
    "page.setup.allow_say_local": ('checked on every connection: 127.0.0.1 and ::1; an SSH tunnel arrives as 127.0.0.1', '每个连接都检查：127.0.0.1 和 ::1；SSH 隧道进来就是 127.0.0.1'),
    "page.setup.allow_say_private": ('⚠️ everyone on your LAN or VPN can try the secret', '⚠️ 局域网或 VPN 里的人都能试密钥'),
    "page.setup.allow_say_public": ('❌ anyone on the internet may connect, and what is sent crosses in plain text', '❌ 互联网上任何人都能连，发送的内容是明文'),
    "page.setup.bind_follows": ('empty: follows who may connect ({b})', '留空：跟随“谁可以连接”（{b}）'),
    "page.setup.instr": ('Peers in the global instructions', '把对端写进全局指令'),
    "page.setup.says": ('It says', '它说'),
    "page.setup.send_only": ('send-only', '只发送'),
    "page.setup.resources": ('Resources: a shared name, your own way there', '资源：共享名字，各自的路径'),
    "page.setup.res_how": ('the peers learn only the name; the address is how THIS machine reaches it and is never sent', '对端只知道名字；地址是本机怎么到达它，从不发送'),
    "page.setup.res": ('Resource', '资源'),
    "page.setup.kind": ('Kind', '类型'),
    "page.setup.res_url": ('How this machine reaches it', '本机怎么到达'),
    "page.setup.here": ('From here', '从本机'),
    "page.setup.also_at": ('Also at', '也在'),
    "page.setup.add_res": ('Add a resource', '添加资源'),
    "page.setup.no_res": ('No resources yet.', '还没有资源。'),
    "page.setup.reaching": ('reaching {name} from here …', '正在从本机连接 {name} …'),
    "page.setup.no_log": ('Nothing logged yet.', '还没有日志。'),
    "page.setup.deleted": ('{name} deleted: write peers.toml to keep it', '已删除 {name}：写入 peers.toml 才会保存'),
    "page.setup.checking_one": ('checking {name} …', '正在检查 {name}…'),
    "page.setup.same_secret": ('{name}: same secret both ways', '{name}：双方密钥一致'),
    "page.connecting": ("connecting…", "连接中…"),
    "page.stop": ("■ STOP", "■ 停止"),
    "page.lang_title": ("the page's language; the chats keep buddy.toml's",
                        "页面语言；聊天仍按 buddy.toml 的设置"),
    "page.stop_title": ("stop everything that is running or waiting, and pause the schedule",
                        "停止一切正在运行或等待的工作，并暂停计划任务"),
    "page.send": ("Send", "发送"),
    "page.h.asks": ("Waiting for you", "等你处理"),
    "page.h.asks_sub": ("the same cards as in Lark and Telegram; the first answer anywhere wins",
                        "与 Lark 和 Telegram 里的卡片相同；任何一处最先的回答为准"),
    "page.h.l1": ("Level 1 · Workflows and their runs", "第 1 层 · 工作流及其运行"),
    "page.h.l1_sub": ("jobs and duties: the last 3 days; stories and teams: all · click one",
                      "任务和职责：最近 3 天；故事和团队：全部 · 点击查看"),
    "page.l2.arch": ('Architecture', '架构'),
    "page.l2.lanes": ('Lanes', '泳道'),
    "page.lanes.only_stories": ('Lanes show a short story: pick one in level 1.', '泳道显示短篇故事：在第 1 层选一个。'),
    "page.lanes.none": ('No step has been marked yet.', '还没有步骤被标记。'),
    "page.lanes.inferred": ('start inferred: only its end was marked', '开始时间是推断的：只标记了结束'),
    "page.lanes.running": ('running', '进行中'),
    "page.lanes.legend": ('machine → session (its row: the main agent) → ↳ subagent (marked by its master with BUDDY_AGENT) · hatched: start inferred (only the end was marked) · gold: running · ⤴ Attach: the session\'s pys window', '机器 → 会话（这一行：主代理）→ ↳ 子代理（由主代理用 BUDDY_AGENT 标记）· 斜纹：开始时间为推断（只标记了结束）· 金色：进行中 · ⤴ 附着：该会话的 pys 窗口'),
    "page.lanes.here": ('this machine', '本机'),
    "page.lanes.fold": ('hide its {n} subagent(s)', '收起它的 {n} 个子代理'),
    "page.lanes.unfold": ('show its {n} subagent(s)', '展开它的 {n} 个子代理'),
    "page.lanes.attach": ('Attach', '附着'),
    "page.lanes.attach_tip": ('bring up pys window {w}, where this session runs', '切到这个会话所在的 pys 窗口 {w}'),
    "page.lanes.attach_gone": ('window {w} no longer holds this session', '窗口 {w} 里已不是这个会话'),
    "page.lanes.attach_there": ('it runs on {m}: attach from its own workbench, or pys attach there', '它在 {m} 上运行：从那边的工作台附着，或在那边 pys attach'),
    "page.lanes.attached": ('pys window {w} is up in your terminal', 'pys 窗口 {w} 已在你的终端里显示'),
    "page.lanes.attach_failed": ('the buddy did not answer', '伙伴没有回应'),
    "page.h.l2": ("Level 2 · How the system works", "第 2 层 · 系统如何运作"),
    "page.h.l2_sub": ("lit by the selected run · click a part", "按所选的运行点亮 · 点击一个部分"),
    "page.h.l3": ("Level 3 · Observation", "第 3 层 · 观察"),
    "page.live": ("live · {n} runs and stories · updated {at}", "在线 · {n} 次运行和故事 · 更新于 {at}"),
    "page.loading": ("loading…", "加载中…"),
    "page.long.attach": ("▶ attach (pys)", "▶ 附着（pys）"),
    "page.long.resume": ("↻ resume in pys", "↻ 在 pys 中恢复"),
    "page.long.done": ("pys window {w}", "pys 窗口 {w}"),
    "page.long.held_screen": ("held by a screen window", "由 screen 窗口持有"),
    "page.long.no_pys": ("attach / resume need pys: uv tool install py-screen", "附着 / 恢复需要 pys：uv tool install py-screen"),
    "page.quota_title": ("Lark quota: reached your other chats; Lark refused it for the month's API quota",
                         "Lark 配额：已发到其他聊天；Lark 因本月 API 配额拒绝"),
    "page.long.codex_by_hand": ("Codex: resume by hand (not mirrored):", "Codex：手动恢复（不镜像）："),
    "page.team.l2h": ("team {team} · its history", "团队 {team} · 历程"),
    "page.team.card": ("{team} · the card, now", "{team} · 卡片（此刻）"),
    "page.team.write": ("write to its RM…", "给它的 RM 写…"),
    "page.team.members": ("Members and their short tasks", "成员与其短链路任务"),
    "page.team.no_stories": ("no short tasks", "没有短链路任务"),
    "page.team.card_text": ("the card as text", "卡片原文"),
    "page.team.recorded_only": ("Only short tasks started by a session the team's records name (a role's generations) are listed.",
                                "只列出团队记录中有名字的会话（角色的各代）发起的短链路任务。"),
    "page.not_found": ("The link's {what} is not here (gone, or mistyped): showing the latest instead.",
                       "链接里的 {what} 不存在（已没有或写错）：显示最新的运行。"),
    "page.story.member": ("member {role} · team {team}", "成员 {role} · 团队 {team}"),
    "page.down": ("the buddy doesn't answer: is buddy serve running?", "buddy 没有响应：buddy serve 在运行吗？"),
    "page.halt": ("■ <b>Stopped from {where}</b> at {at}. The schedule starts nothing new until you resume. ",
                  "■ <b>已从{where}停止</b>，时间 {at}。在你恢复之前，计划任务不会启动任何新的运行。"),
    "page.resume": ("▶ Resume", "▶ 恢复"),
    "page.stop_btn": ("■ Stop", "■ 停止"),
    "page.nothing_waiting": ("Nothing is waiting for you.", "没有等你处理的事。"),
    "page.on": ("on {where} · {at}", "{where} · {at}"),
    "page.status.running": ("⏳ running", "⏳ 运行中"),
    "page.status.quota": ("⚠ quota", "⚠ 配额"),
    "page.status.stopped": ("■ stopped", "■ 已停止"),
    "page.status.chat": ("💬 chat", "💬 聊天"),
    "page.status.quiet": ("· quiet", "· 无事"),
    "page.status.posted": ("✓ posted", "✓ 已发出"),
    "page.status.ok": ("✓ ok", "✓ 成功"),
    "page.st.ok": ("ok", "成功"),
    "page.st.failed": ("failed", "失败"),
    "page.st.quota": ("quota", "Lark 配额（已发到其他聊天）"),
    "page.st.timeout": ("timeout", "超时"),
    "page.st.interrupted": ("interrupted", "中断"),
    "page.st.stopped": ("stopped", "已停止"),
    "page.st.running": ("running", "运行中"),
    "page.kind.job": ("job", "任务"),
    "page.kind.duty": ("duty", "职责"),
    "page.kind.family": ("family", "家族"),
    "page.kind.chat": ("chat", "聊天"),
    "page.app.lark": ("lark", "Lark"),
    "page.app.telegram": ("telegram", "Telegram"),
    "page.app.web": ("web", "网页"),
    "page.app.chat": ("chat", "聊天"),
    "page.part.input": ("input", "输入"),
    "page.part.effect": ("effect", "效果"),
    "page.part.tools": ("tools", "技能与工具"),
    "page.part.responsible": ("responsible", "责任"),
    "page.part.observability": ("observability", "可观测性"),
    "page.running_now": ("⏳ running now", "⏳ 正在运行"),
    "page.wf.chat": (" · your messages, from any app", " · 你的消息，来自任一应用"),
    "page.wf.unscheduled": (" · not scheduled", " · 未排计划"),
    "page.wf.stories": ("{n} stories", "{n} 个故事"),
    "page.wf.teams": ("{n} teams", "{n} 个团队"),
    "page.wf.runs": ("{n} runs", "{n} 次运行"),
    "page.wf.failed": ("{n} failed", "{n} 次失败"),
    "page.wf.code_only": ("$0 · code only", "$0 · 仅代码"),
    "page.wf.tok": ("{n} tok", "{n} 词元"),
    "page.wf.denied": ("{n} denied", "{n} 次拒绝"),
    "page.wf.quiet": ("+{n} quiet", "+{n} 次无事"),
    "page.no_runs": ("No runs yet.", "还没有运行。"),
    "page.l2h": ("{kind} {name} · {at} · click a part", "{kind} {name} · {at} · 点击一个部分"),
    "page.resp": ("Responsible · who answers for it", "责任 · 谁对它负责"),
    "page.resp.failed": ("this run {status}: you are called in", "这次运行{status}：需要你出面"),
    "page.resp.asked.one": ("it asked you {n} question", "它问了你 {n} 个问题"),
    "page.resp.asked.many": ("it asked you {n} questions", "它问了你 {n} 个问题"),
    "page.resp.none": ("nothing needed you", "没有需要你的"),
    "page.resp.stop": (" · ■ STOP is always in reach", " · ■ 停止随时可按"),
    "page.input": ("Input · what it responded to", "输入 · 它回应的是什么"),
    "page.step": ("Step {n}", "第 {n} 步"),
    "page.no_steps": ("no steps recorded", "没有记录步骤"),
    "page.effect": ("Effect · on the world", "效果 · 对外界"),
    "page.posted_to": ("posted to {n} chat(s)", "已发到 {n} 个聊天"),
    "page.tools": ("Skills & tools", "技能与工具"),
    "page.obs": ("Observability · the foundation", "可观测性 · 基础"),
    "page.obs.record": ("run record {id} · version {version}", "运行记录 {id} · 版本 {version}"),
    "page.obs.box": (" · black box kept", " · 已保留黑匣子"),
    "page.whole_run": ("the whole run", "整次运行"),
    "page.sop.own": ("it recorded a step its SOP doesn't name, so this is its own steps",
                     "它记录了 SOP 里没有的步骤，所以这里画的是它自己的步骤"),
    "page.sop.none": ("no SOP for {kind}: its own steps", "{kind} 没有 SOP：画的是它自己的步骤"),
    "page.d.changes.one": ("{n} change", "{n} 处变化"),
    "page.d.changes.many": ("{n} changes", "{n} 处变化"),
    "page.d.on": ("{choice} on {where}", "{choice}，在{where}"),
    "page.d.to.one": ("to {n} chat", "发到 {n} 个聊天"),
    "page.d.to.many": ("to {n} chats", "发到 {n} 个聊天"),
    "page.d.not_all": (" · not all reached", " · 未全部送达"),
    "page.d.chars": ("{n} chars", "{n} 个字符"),
    "page.d.turns.one": ("{n} turn", "{n} 轮"),
    "page.d.turns.many": ("{n} turns", "{n} 轮"),
    "page.d.no_cost": ("no cost recorded", "没有记录开销"),
    "page.d.denied": (" · denied {tools}", " · 拒绝了 {tools}"),
    "page.end.running": ("nothing yet: still running", "还没有：仍在运行"),
    "page.end.quiet": ("nothing: no changes", "什么也没有：没有变化"),
    "page.end.notice.one": ("a notice in {n} chat", "{n} 个聊天里的一条通知"),
    "page.end.notice.many": ("a notice in {n} chats", "{n} 个聊天里的一条通知"),
    "page.end.chat": ("the answer, in your chat and mirrored", "答案，在你的聊天里，并同步到其他应用"),
    "page.fit.s": ("⏱ {s} s", "⏱ {s} 秒"),
    "page.fit.tokens": ("{n} tokens", "{n} 词元"),
    "page.fit.no_agent": ("no agent step: $0", "没有智能体步骤：$0"),
    "page.fit.denied": ("{n} denied", "{n} 次拒绝"),
    "page.sketch": ("Sketch · the path this run took", "草图 · 这次运行走过的路径"),
    "page.sketch_sub": ("solid: taken · dashed: not taken · under a step: what the run record says",
                        "实线：走过 · 虚线：未走 · 步骤下方：运行记录里的内容"),
    "page.story.l2h": ("short story #{n} · {title}", "短链路任务 #{n} · {title}"),
    "page.story.stop": ("■ stop asked from {where} at {at}", "■ 已从{where}请求停止，时间 {at}"),
    "page.story.unread.one": ("💬 {n} reply not read by the executor yet", "💬 {n} 条回复执行者尚未读取"),
    "page.story.unread.many": ("💬 {n} replies not read by the executor yet", "💬 {n} 条回复执行者尚未读取"),
    "page.story.you": ("you: ■ Stop or 💬 Reply below, or under its card in Lark and Telegram",
                       "你：在下面按 ■ 停止 或 💬 回复，或在 Lark 和 Telegram 的卡片下操作"),
    "page.story.ended": ("it ended", "已结束"),
    "page.story.input": ("Input · what started it", "输入 · 由什么发起"),
    "page.story.mark": ("Mark {n}", "节点 {n}"),
    "page.story.unplanned": (" · not planned", " · 计划外"),
    "page.story.no_marks": ("no marks", "没有节点"),
    "page.story.ok": ("🏁 done", "🏁 完成"),
    "page.story.failed": ("❌ failed", "❌ 失败"),
    "page.story.stopped": ("■ stopped by the owner", "■ 被负责人停止"),
    "page.story.running": ("⏳ running", "⏳ 运行中"),
    "page.story.file": ("the story file {file} · {n} min", "故事文件 {file} · {n} 分钟"),
    "page.story.card": ("#{n} · the card", "#{n} · 卡片"),
    "page.story.placeholder": ("A reply to the executor: it reads it at its next step",
                               "给执行者的回复：它会在下一步读到"),
    "page.story.send": ("💬 Send", "💬 发送"),
    "page.replies": ("Replies", "回复"),
    "page.read": ("read", "已读"),
    "page.unread": ("not read yet", "尚未读取"),
    "page.none": ("none", "无"),
    "page.story_file": ("The story file", "故事文件"),
    "page.fitness": ("Fitness", "适应度"),
    "page.fit.list": ("{usd} at list price · {turns} turns · {tokens} tokens",
                      "按标价 {usd} · {turns} 轮 · {tokens} 词元"),
    "page.fit.none": ("no agent step: $0, 0 tokens", "没有智能体步骤：$0，0 词元"),
    "page.black_box": ("Black box", "黑匣子"),
    "page.no_output": ("(no output kept)", "（没有保留输出）"),
    "page.handoff": ("Continue this run elsewhere", "在别处继续这次运行"),
    "page.handoff.terminal": ("terminal", "终端"),
    "page.handoff.same": ("(the same session)", "（同一个会话）"),
    "page.handoff.codex": ("a new thread, seeded from this record: not a resume",
                           "以这条记录为起点的新线程：不是续接"),
    "page.step_h": ("Step · {name}", "步骤 · {name}"),
    "page.input_h": ("Input", "输入"),
    "page.input_p": ("A timer and a person's message are the same thing here: something the system responds to.",
                     "在这里，定时器和人的消息是一回事：都是系统要回应的东西。"),
    "page.effect_h": ("Effect on the world", "对外界的效果"),
    "page.resp_h": ("Responsible", "责任"),
    "page.resp.this_run": ("this run", "这次运行"),
    "page.resp.asked_you": ("asked you", "问了你"),
    "page.resp.answers": ("answers", "回答"),
    "page.resp.nothing": ("nothing", "没有"),
    "page.resp_p": ("The top is who answers for the work, and who can stop it, from any app.",
                    "顶部是对这项工作负责的人，也是能从任一应用停止它的人。"),
    "page.chat.main": ("💬 Main", "💬 主对话"),
    "page.chat.main_title": ("the conversation your Lark and Telegram chats share",
                             "你的 Lark 和 Telegram 聊天共享的对话"),
    "page.chat.topic_title": ("{id} · started on {where} · its own session", "{id} · 在{where}发起 · 有自己的会话"),
    "page.chat.new_topic": ("a new topic: its name", "新话题：它的名称"),
    "page.chat.add_topic": ("＋ Topic", "＋ 话题"),
    "page.chat.add_topic_title": ("a conversation with its own session; a thread in Lark too",
                                  "一段有自己会话的对话；在 Lark 里也是一个话题"),
    "page.chat.say_topic": ("Message this topic: its own session, mirrored to its Lark thread. Enter sends, "
                            "Shift+Enter is a new line.",
                            "给这个话题发消息：它有自己的会话，并同步到 Lark 的话题。Enter 发送，Shift+Enter 换行。"),
    "page.chat.say": ("Message the buddy: the same session as Lark and Telegram, mirrored to them. Enter "
                      "sends, Shift+Enter is a new line.",
                      "给 buddy 发消息：与 Lark 和 Telegram 同一个会话，并同步过去。Enter 发送，Shift+Enter 换行。"),
    "page.chat.off": ("The web chat needs [web] port and [me] chats in buddy.toml.",
                      "网页聊天需要在 buddy.toml 里设置 [web] port 和 [me] chats。"),
    "page.chat.prefill": ("writes /short {n} in the box: a reply to that story's executor",
                          "在输入框里写入 /short {n}：回复该故事的执行者"),
    "page.chat.empty": ("Say something. It goes to the same session as your Lark and Telegram chats, and "
                        "they see it as \"↔ from web\".",
                        "说点什么吧。它会进入与你的 Lark 和 Telegram 聊天相同的会话，它们会看到「↔ 来自网页」。"),
}
