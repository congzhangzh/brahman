"""Duties: the standing jobs a brahman does, each a package with its own SOP (DUTY.md).

A duty is configured under [duties.<name>] in buddy.toml. `buddy duty <name>` runs one
check and posts what changed to the duty's chat_id (`buddy serve` runs it on the
duty's schedule); its chat commands are added to the bot.
"""

from .code_review import CodeReview
from .threads import Threads

DUTIES = {d.name: d for d in (CodeReview, Threads)}
