"""TCP-Reno-style congestion-control state machine (sender side only).

Window unit is "segments" (1 segment = 1 packet). cwnd is a float so the
congestion-avoidance increment ``1/cwnd`` per ACK is exact.

Window update rules implemented here
------------------------------------
Slow start (per newly ACKed segment):      cwnd += 1
Congestion avoidance (per newly ACKed seg): cwnd += 1/cwnd
3rd duplicate ACK (fast retransmit):       ssthresh = max(cwnd/2, 2)
                                           cwnd = ssthresh + 3   (inflate)
Further dup ACKs in fast recovery:         cwnd += 1             (inflate)
New ACK ending fast recovery:              cwnd = ssthresh       (deflate)
Timeout:                                   ssthresh = max(cwnd/2, 2)
                                           cwnd = 1, back to SLOW_START
"""

from enum import Enum


class State(Enum):
    SLOW_START = "SLOW_START"
    CONGESTION_AVOIDANCE = "CONGESTION_AVOIDANCE"
    FAST_RECOVERY = "FAST_RECOVERY"


class Sender:
    def __init__(self, total_segments, init_cwnd=1.0, init_ssthresh=64.0,
                 rto=2.0, max_backoff=16):
        self.total = total_segments
        self.cwnd = float(init_cwnd)
        self.ssthresh = float(init_ssthresh)
        self.state = (State.CONGESTION_AVOIDANCE
                      if init_cwnd >= init_ssthresh else State.SLOW_START)

        self.next_seq = 0      # next segment number to transmit
        self.send_base = 0     # oldest unacknowledged segment (cumulative)
        self.dup_acks = 0

        self.retransmits = 0   # number of loss-recovery retransmission events
        self.timeouts = 0

        self.rto_base = float(rto)
        self.rto = float(rto)          # current RTO (grows on backoff)
        self.max_backoff = max_backoff  # cap on the backoff multiplier

    @property
    def inflight(self):
        return self.next_seq - self.send_base

    @property
    def done(self):
        return self.send_base >= self.total

    def can_send(self):
        return self.next_seq < self.total and self.inflight < max(1, int(self.cwnd))

    def mark_sent(self):
        self.next_seq += 1

    # -- ACK processing ----------------------------------------------------

    def on_new_ack(self, ack):
        """Handle a cumulative ACK that advances send_base. Returns events."""
        newly = ack - self.send_base
        self.send_base = ack
        self.dup_acks = 0
        self.rto = self.rto_base  # progress resets the backoff
        events = []
        if self.state is State.FAST_RECOVERY:
            # Basic fast recovery: a new ACK deflates cwnd back to ssthresh.
            self.cwnd = self.ssthresh
            self.state = State.CONGESTION_AVOIDANCE
            events.append("EXIT_FAST_RECOVERY")
        elif self.state is State.SLOW_START:
            self.cwnd += newly  # +1 per ACKed segment: exponential growth
            if self.cwnd >= self.ssthresh:
                self.state = State.CONGESTION_AVOIDANCE
                events.append("ENTER_CONGESTION_AVOIDANCE")
        else:
            self.cwnd += newly / self.cwnd  # ~+1 segment per RTT
        return events

    def on_dup_ack(self):
        """Handle a duplicate ACK. Returns (events, retransmit_seq|None)."""
        self.dup_acks += 1
        events = []
        retransmit = None
        if self.state is State.FAST_RECOVERY:
            self.cwnd += 1.0  # inflate: one more segment has left the pipe
            events.append("INFLATE")
        elif self.dup_acks == 3:
            self.ssthresh = max(self.cwnd / 2.0, 2.0)
            self.cwnd = self.ssthresh + 3.0
            self.state = State.FAST_RECOVERY
            self.retransmits += 1
            retransmit = self.send_base
            events.append("FAST_RETRANSMIT")
        return events, retransmit

    def on_timeout(self):
        """Handle RTO expiry: go-back-N retransmit, re-enter slow start."""
        self.timeouts += 1
        self.retransmits += 1
        self.ssthresh = max(self.cwnd / 2.0, 2.0)
        self.cwnd = 1.0
        self.dup_acks = 0
        self.state = State.SLOW_START
        self.next_seq = self.send_base  # go-back-N
        self.rto = min(self.rto * 2.0, self.rto_base * self.max_backoff)
        return ["ENTER_SLOW_START"]
