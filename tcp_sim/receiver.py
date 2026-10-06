"""Cumulative-ACK receiver with a small reordering buffer (like real TCP)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Receiver:
    next_expected: int = 0
    buffered: set = field(default_factory=set)
    received: int = 0

    def on_packet(self, seq: int) -> int:
        """Consume a data packet; return the cumulative ACK number to send."""
        self.received += 1
        if seq == self.next_expected:
            self.next_expected += 1
            while self.next_expected in self.buffered:
                self.buffered.discard(self.next_expected)
                self.next_expected += 1
        elif seq > self.next_expected:
            self.buffered.add(seq)
        # seq < next_expected: duplicate of an already-acked packet.
        return self.next_expected
