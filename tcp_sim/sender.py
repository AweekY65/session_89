"""TCP Reno-style sender: cwnd / ssthresh / in-flight / ACK bookkeeping.

The sender never touches real time or real sockets. It is driven entirely
by the simulation engine which calls ``on_ack`` / ``on_timeout`` and asks
the sender which packets it wants to (re)transmit.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field


class State(enum.Enum):
    SLOW_START = "SLOW_START"
    CONGESTION_AVOIDANCE = "CONGESTION_AVOIDANCE"
    FAST_RECOVERY = "FAST_RECOVERY"


@dataclass
class SendDecision:
    """A packet the sender wants to put on the wire right now."""
    seq: int
    is_retransmit: bool


@dataclass
class Sender:
    total_packets: int
    initial_ssthresh: float = 16.0
    # State the sender enters after a timeout (SLOW_START is the classic
    # choice; CONGESTION_AVOIDANCE models "specified recovery state").
    timeout_recovery_state: State = State.SLOW_START

    cwnd: float = 1.0
    ssthresh: float = field(init=False)
    state: State = field(init=False, default=State.SLOW_START)

    next_seq: int = 0            # next never-sent sequence number
    send_base: int = 0           # oldest unacknowledged sequence number
    dup_ack_count: int = 0
    retransmits: int = 0
    acked_packets: int = 0       # cumulative newly-acked packets (for throughput)

    unacked: set = field(init=False, default_factory=set)

    def __post_init__(self):
        self.ssthresh = self.initial_ssthresh

    # ------------------------------------------------------------------ #
    # window / in-flight helpers
    # ------------------------------------------------------------------ #
    @property
    def in_flight(self) -> int:
        return len(self.unacked)

    @property
    def effective_window(self) -> int:
        return max(1, math.floor(self.cwnd))

    def window_available(self) -> int:
        return max(0, self.effective_window - self.in_flight)

    def pending_sends(self) -> list[SendDecision]:
        """New packets the sender may transmit given the current window."""
        out = []
        while self.window_available() > 0 and self.next_seq < self.total_packets:
            out.append(SendDecision(seq=self.next_seq, is_retransmit=False))
            self.unacked.add(self.next_seq)
            self.next_seq += 1
        return out

    # ------------------------------------------------------------------ #
    # ACK processing — the heart of the congestion controller
    # ------------------------------------------------------------------ #
    def on_ack(self, ack_num: int) -> list[str]:
        """Process a cumulative ACK. Returns human-readable transition notes."""
        notes: list[str] = []
        if ack_num > self.send_base:
            # ---- new ACK: advances the window --------------------------
            newly = ack_num - self.send_base
            self.acked_packets += newly
            self.unacked = {s for s in self.unacked if s >= ack_num}
            self.send_base = ack_num
            self.dup_ack_count = 0

            if self.state is State.FAST_RECOVERY:
                # Deflate: partial/full ACK ends fast recovery (Reno).
                self.cwnd = self.ssthresh
                self.state = State.CONGESTION_AVOIDANCE
                notes.append(
                    f"new ACK in FAST_RECOVERY: deflate cwnd -> ssthresh "
                    f"({self.cwnd:.2f}), enter CONGESTION_AVOIDANCE"
                )
            elif self.state is State.SLOW_START:
                # +1 MSS per ACKed packet (exponential growth per RTT).
                self.cwnd += newly
                notes.append(f"slow start: cwnd += {newly} -> {self.cwnd:.2f}")
                if self.cwnd >= self.ssthresh:
                    self.state = State.CONGESTION_AVOIDANCE
                    notes.append(
                        f"cwnd >= ssthresh ({self.ssthresh:.2f}): "
                        f"enter CONGESTION_AVOIDANCE"
                    )
            else:
                # Congestion avoidance: +1 MSS per RTT => += newly/cwnd.
                inc = newly / self.cwnd
                self.cwnd += inc
                notes.append(
                    f"congestion avoidance: cwnd += {inc:.4f} -> {self.cwnd:.2f}"
                )
        elif ack_num == self.send_base and self.send_base < self.total_packets:
            # ---- duplicate ACK ------------------------------------------
            self.dup_ack_count += 1
            if self.state is State.FAST_RECOVERY:
                # Inflate: one more packet has left the network.
                self.cwnd += 1
                notes.append(
                    f"dup ACK #{self.dup_ack_count} in FAST_RECOVERY: "
                    f"inflate cwnd -> {self.cwnd:.2f}"
                )
            else:
                notes.append(f"dup ACK #{self.dup_ack_count} (cwnd unchanged)")
                if self.dup_ack_count == 3:
                    notes.extend(self._fast_retransmit())
        return notes

    def _fast_retransmit(self) -> list[str]:
        self.ssthresh = max(self.cwnd / 2.0, 2.0)
        self.cwnd = self.ssthresh + 3.0   # inflate by the 3 dup ACKs
        self.state = State.FAST_RECOVERY
        return [
            f"3 duplicate ACKs: fast retransmit seq={self.send_base}, "
            f"ssthresh={self.ssthresh:.2f}, cwnd=ssthresh+3={self.cwnd:.2f}, "
            f"enter FAST_RECOVERY"
        ]

    def fast_retransmit_packet(self) -> SendDecision:
        self.retransmits += 1
        return SendDecision(seq=self.send_base, is_retransmit=True)

    # ------------------------------------------------------------------ #
    # timeout
    # ------------------------------------------------------------------ #
    def on_timeout(self) -> tuple[SendDecision, list[str]]:
        self.ssthresh = max(self.cwnd / 2.0, 2.0)
        self.cwnd = 1.0
        self.dup_ack_count = 0
        self.state = self.timeout_recovery_state
        self.retransmits += 1
        notes = [
            f"timeout: ssthresh={self.ssthresh:.2f}, cwnd=1.00, "
            f"retransmit seq={self.send_base}, enter {self.state.value}"
        ]
        return SendDecision(seq=self.send_base, is_retransmit=True), notes

    @property
    def done(self) -> bool:
        return self.send_base >= self.total_packets
