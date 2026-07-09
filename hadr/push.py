"""The push sink — an injected dumb edge, like ``FeedSource`` and ``Clock``.

The urgent-alert *decision* is deterministic core (``alert.decide_alert``); the
*delivery* is a side effect at the edge, so it is injected into ``pipeline.run``.
A production sink would wrap the harness's native push; that real-network path is
deliberately out of scope for this slice. Tests inject ``RecordingPushSink`` and
assert the upstream decision as data — the sink is verified, not exercised for
real.
"""

from typing import Protocol

from .alert import Alert


class PushSink(Protocol):
    def send(self, alert: Alert) -> None:
        ...


class RecordingPushSink:
    """A sink that just records what it was asked to send, in order. The whole
    Slice-4 assertion surface: which alerts fired, at what level, with what
    message."""

    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.sent.append(alert)
