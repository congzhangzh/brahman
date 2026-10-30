"""Families: people-led work, each a package with its own SOP (FAMILY.md).

A family is the SOP we write once; a story is one instance of it, whose content differs.
Duties repeat the same steps by themselves; in a family's story the owner leads and the
turn passes between the owner, an executor and the outside world. A family's chat
commands are added to the bot; `buddy stories` prints every story in a terminal.

A family may be configured under [families.<name>] with a `schedule`: then `buddy serve`
runs `buddy family <name>`, which posts the family's scheduled() Markdown (the monthly
report on the 1st, for example) to chat_id or to every chat of [me].
"""

from .long_story import LongStory
from .monthly_report import MonthlyReport
from .short_story import ShortStory
from .upstream_mr import UpstreamMR

FAMILIES = {f.name: f for f in (UpstreamMR, MonthlyReport, ShortStory, LongStory)}
