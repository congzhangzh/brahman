"""The Lark/Feishu channel without Lark's SDK until a call needs it.

Importing `lark_oapi` loads about 10,800 modules: 3.3 s on this machine (2026-11-14, cProfile),
paid by every `buddy-short` command, mark and tool hook, since cli built the channel up front. Most
of them never talk to Lark: with [notify] off for Lark and Feishu (the default) a card is kept back
before any call. So the channel answers what needs no SDK itself (name, prefix, owns) and builds
the real Feishu (feishu.py) on the first attribute that needs it.
"""

from __future__ import annotations


class LazyFeishu:
    def __init__(self, app_id: str, app_secret: str, domain: str = "feishu", prefix: str = ""):
        object.__setattr__(self, "_args", (app_id, app_secret, domain, prefix))
        object.__setattr__(self, "_real", None)
        object.__setattr__(self, "name", "feishu" if domain == "feishu" else "lark")
        object.__setattr__(self, "prefix", prefix)
        object.__setattr__(self, "counter", None)

    def owns(self, chat: str) -> bool:  # the same rule as Feishu.owns
        return chat.startswith(self.prefix + "oc_")

    @property
    def loaded(self) -> bool:
        return self._real is not None

    def _get(self):
        if self._real is None:
            from .feishu import Feishu
            app_id, secret, domain, prefix = self._args
            real = Feishu(app_id, secret, domain, prefix=prefix)
            real.counter = self.counter
            object.__setattr__(self, "_real", real)
        return self._real

    def __setattr__(self, key, value):  # counter (cli._quiet) and anything set later reach the real one too
        object.__setattr__(self, key, value)
        if self._real is not None:
            setattr(self._real, key, value)

    def __getattr__(self, key):  # only for what this class doesn't have: the SDK's calls
        if key.startswith("__"):
            raise AttributeError(key)
        return getattr(self._get(), key)
