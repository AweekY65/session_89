"""Event-driven simulation engine with a virtual clock.

Events live in a heap ordered by (virtual_time, insertion_counter), so
ties are broken deterministically by insertion order. The clock only ever
advances by popping events — there is no real sleep anywhere.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .network import Network, NetworkConfig
from .receiver import Receiver
from .sender import Sender, State


@dataclass
class SimConfig:
    total_packets: int = 50
    initial_ssthresh: float = 16.0
    rto_multiplier: float = 2.0        # RTO = rto_multiplier * RTT
    min_rto: float = 1.0
    max_time: float = 1_000_000.0      # virtual-time safety bound
    max_events: int = 1_000_000        # event-count safety bound
    timeout_recovery_state: State = State.SLOW_START
    network: NetworkConfig = field(default_factory=NetworkConfig)


@dataclass
class LogEntry:
    time: float
    event: str
    cwnd: float
    ssthresh: float
    state: str
    in_flight: int
    retransmits: int
    throughput: float                  # packets acked per virtual time unit

    def row(self) -> str:
        return (
            f"t={self.time:9.2f}  cwnd={self.cwnd:7.2f}  "
            f"ssthresh={self.ssthresh:7.2f}  state={self.state:<22} "
            f"in_flight={self.in_flight:3d}  rtx={self.retransmits:3d}  "
            f"tput={self.throughput:8.4f}  {self.event}"
        )


class Simulation:
    def __init__(self, config: SimConfig):
        self.config = config
        self.network = Network(config.network)
        self.sender = Sender(
            total_packets=config.total_packets,
            initial_ssthresh=config.initial_ssthresh,
            timeout_recovery_state=config.timeout_recovery_state,
        )
        self.receiver = Receiver()
        self.now = 0.0
        self._heap: list = []
        self._counter = 0
        self._timer_gen = 0              # bumped to invalidate pending RTO timers
        self._rto_backoff = 1.0
        self.log: list[LogEntry] = []

    # ------------------------------------------------------------------ #
    def _schedule(self, time: float, kind: str, payload):
        heapq.heappush(self._heap, (time, self._counter, kind, payload))
        self._counter += 1

    def _current_rto(self) -> float:
        base = max(self.config.rto_multiplier * self.config.network.rtt,
                   self.config.min_rto)
        return base * self._rto_backoff

    def _arm_timer(self):
        """(Re)start the RTO timer for the oldest unacked packet."""
        self._timer_gen += 1
        self._schedule(self.now + self._current_rto(), "timeout",
                       (self._timer_gen, self.sender.send_base))

    def _record(self, event: str):
        tput = self.sender.acked_packets / self.now if self.now > 0 else 0.0
        self.log.append(LogEntry(
            time=self.now,
            event=event,
            cwnd=self.sender.cwnd,
            ssthresh=self.sender.ssthresh,
            state=self.sender.state.value,
            in_flight=self.sender.in_flight,
            retransmits=self.sender.retransmits,
            throughput=tput,
        ))

    # ------------------------------------------------------------------ #
    def _transmit(self, decision):
        if decision.is_retransmit:
            self.sender.unacked.add(decision.seq)
        deliver_at = self.network.transmit_data(decision.seq, self.now)
        if deliver_at is not None:
            self._schedule(deliver_at, "packet", decision.seq)

    def _pump(self):
        sent = self.sender.pending_sends()
        for decision in sent:
            self._transmit(decision)
        if sent and self.sender.unacked:
            self._arm_timer()

    # ------------------------------------------------------------------ #
    def run(self) -> list[LogEntry]:
        self._record("init: slow start, cwnd=1")
        self._pump()
        events = 0
        while self._heap and not self.sender.done:
            if self.now > self.config.max_time or events >= self.config.max_events:
                self._record("aborted: safety bound reached")
                break
            time, _, kind, payload = heapq.heappop(self._heap)
            self.now = time
            events += 1
            if kind == "packet":
                self._on_packet(payload)
            elif kind == "ack":
                self._on_ack(payload)
            elif kind == "timeout":
                self._on_timeout(*payload)
            self._pump()
        if self.sender.done:
            self._record("done: all packets acknowledged")
        return self.log

    # ------------------------------------------------------------------ #
    def _on_packet(self, seq: int):
        ack_num = self.receiver.on_packet(seq)
        deliver_at = self.network.transmit_ack(ack_num, self.now)
        if deliver_at is not None:
            self._schedule(deliver_at, "ack", ack_num)

    def _on_ack(self, ack_num: int):
        prev_base = self.sender.send_base
        notes = self.sender.on_ack(ack_num)
        if any("fast retransmit" in n for n in notes):
            self._transmit(self.sender.fast_retransmit_packet())
            self._arm_timer()
        for note in notes:
            self._record(f"ACK {ack_num}: {note}")
        if self.sender.send_base != prev_base:
            self._rto_backoff = 1.0
            if self.sender.unacked:
                self._arm_timer()

    def _on_timeout(self, gen: int, guarded_seq: int):
        if gen != self._timer_gen or not self.sender.unacked:
            return  # stale timer
        if guarded_seq != self.sender.send_base:
            return  # window already advanced; a fresh timer is armed
        decision, notes = self.sender.on_timeout()
        self._rto_backoff *= 2.0
        for note in notes:
            self._record(note)
        self._transmit(decision)
        self._arm_timer()

    # ------------------------------------------------------------------ #
    def summary(self) -> str:
        s = self.sender
        n = self.network
        return (
            f"finished at t={self.now:.2f} (virtual), "
            f"acked={s.acked_packets}/{s.total_packets}, "
            f"retransmits={s.retransmits}, "
            f"packets_dropped={n.dropped_packets}, acks_dropped={n.dropped_acks}, "
            f"reordered={n.reordered}, final_state={s.state.value}, "
            f"final_cwnd={s.cwnd:.2f}"
        )
