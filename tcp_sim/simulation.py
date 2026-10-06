"""Simulation driver: wires Sender + Network + Receiver on a VirtualClock."""

from collections import namedtuple

from .clock import VirtualClock
from .network import Network, Receiver
from .sender import Sender

Entry = namedtuple("Entry", ["time", "event", "cwnd", "ssthresh", "inflight", "state"])


class Simulation:
    def __init__(self, total_segments=20, rtt=1.0, loss=0.0, ack_loss=0.0,
                 reorder=0.0, seed=0, init_cwnd=1.0, init_ssthresh=64.0,
                 rto=None, drop_packets=(), drop_acks=()):
        self.clock = VirtualClock()
        self.network = Network(self.clock, rtt, loss=loss, ack_loss=ack_loss,
                               reorder=reorder, seed=seed,
                               drop_packets=drop_packets, drop_acks=drop_acks)
        self.sender = Sender(total_segments, init_cwnd=init_cwnd,
                             init_ssthresh=init_ssthresh,
                             rto=rto if rto is not None else 2.0 * rtt)
        self.receiver = Receiver()
        self.timeline = []
        self.timeout_next_rtos = []   # RTO value after each timeout (backoff)
        self.finish_time = None
        self._timer_gen = 0

    # -- wiring ------------------------------------------------------------

    def _log(self, event):
        s = self.sender
        self.timeline.append(Entry(self.clock.now, event, s.cwnd, s.ssthresh,
                                   s.inflight, s.state.value))

    def _pump(self):
        while self.sender.can_send():
            seq = self.sender.next_seq
            self.sender.mark_sent()
            self.network.send_packet(seq, self._on_packet)

    def _on_packet(self, seq):
        ack = self.receiver.on_packet(seq)
        self.network.send_ack(ack, self._on_ack)

    def _on_ack(self, ack):
        s = self.sender
        if ack > s.send_base:
            newly = ack - s.send_base
            events = s.on_new_ack(ack)
            self._log(f"ACK ack={ack} newly={newly}")
            for event in events:
                self._log(event)
        else:
            events, retransmit = s.on_dup_ack()
            self._log(f"DUP_ACK n={s.dup_acks}")
            for event in events:
                self._log(event)
            if retransmit is not None:
                self.network.send_packet(retransmit, self._on_packet)
        self._pump()
        self._refresh_timer()

    def _refresh_timer(self):
        self._timer_gen += 1
        if not self.sender.done and self.sender.inflight > 0:
            self.clock.schedule(self.sender.rto, self._on_timeout, self._timer_gen)

    def _on_timeout(self, generation):
        if generation != self._timer_gen or self.sender.done:
            return  # stale timer, superseded by a newer one
        s = self.sender
        events = s.on_timeout()
        self.timeout_next_rtos.append(s.rto)
        self._log(f"TIMEOUT next_rto={s.rto}")
        for event in events:
            self._log(event)
        self._pump()
        self._refresh_timer()

    # -- running -----------------------------------------------------------

    def run(self, max_time=1e6):
        self._pump()
        self._refresh_timer()
        while not self.sender.done and self.clock.has_events():
            nxt = self.clock.peek_time()
            if nxt is None or nxt > max_time:
                break
            self.clock.run_next()
        if self.sender.done:
            self.finish_time = self.clock.now
            self._log("COMPLETE")
        return self

    @property
    def completed(self):
        return self.sender.done

    @property
    def throughput(self):
        """Segments per virtual time unit, measured over the whole transfer."""
        if not self.completed or not self.finish_time:
            return 0.0
        return self.sender.total / self.finish_time

    def summary(self):
        s = self.sender
        return {
            "completed": self.completed,
            "finish_time": self.finish_time,
            "throughput": self.throughput,
            "retransmits": s.retransmits,
            "timeouts": s.timeouts,
            "final_cwnd": s.cwnd,
            "final_ssthresh": s.ssthresh,
            "network": dict(self.network.stats),
            "out_of_order_received": self.receiver.out_of_order_count,
        }
