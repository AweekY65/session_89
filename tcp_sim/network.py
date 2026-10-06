"""Deterministic in-memory network and receiver.

The network only moves events through the VirtualClock. It never touches
sockets, the OS TCP stack, the wall clock, or any external service.

Configurable impairments (all driven by a seeded RNG -> deterministic):
  * ``loss``      : probability of dropping a data packet
  * ``ack_loss``  : probability of dropping an ACK
  * ``reorder``   : probability of adding extra jitter delay to a packet
  * ``drop_packets`` / ``drop_acks`` : deterministic one-shot drops of the
    first transmission of a given segment / ACK number (for exact tests)
"""

import random


class Network:
    def __init__(self, clock, rtt, loss=0.0, ack_loss=0.0, reorder=0.0,
                 seed=0, drop_packets=(), drop_acks=()):
        self.clock = clock
        self.one_way = rtt / 2.0
        self.loss = loss
        self.ack_loss = ack_loss
        self.reorder = reorder
        self.rng = random.Random(seed)
        self.drop_packets = set(drop_packets)
        self.drop_acks = set(drop_acks)
        self._packet_sends = {}
        self._ack_sends = {}
        self.stats = {"packets_dropped": 0, "acks_dropped": 0, "reordered": 0}

    def send_packet(self, seq, deliver):
        """Inject a data packet; ``deliver(seq)`` fires on arrival."""
        count = self._packet_sends.get(seq, 0)
        self._packet_sends[seq] = count + 1
        if (count == 0 and seq in self.drop_packets) or self.rng.random() < self.loss:
            self.stats["packets_dropped"] += 1
            return
        delay = self.one_way
        if self.reorder > 0.0 and self.rng.random() < self.reorder:
            delay += self.rng.uniform(0.0, self.one_way)
            self.stats["reordered"] += 1
        self.clock.schedule(delay, deliver, seq)

    def send_ack(self, ack, deliver):
        """Inject an ACK; ``deliver(ack)`` fires on arrival at the sender."""
        count = self._ack_sends.get(ack, 0)
        self._ack_sends[ack] = count + 1
        if (count == 0 and ack in self.drop_acks) or self.rng.random() < self.ack_loss:
            self.stats["acks_dropped"] += 1
            return
        self.clock.schedule(self.one_way, deliver, ack)


class Receiver:
    """Cumulative-ACK receiver with out-of-order buffering (like TCP)."""

    def __init__(self):
        self.expected = 0          # next in-order segment wanted
        self.buffered = set()      # out-of-order segments already received
        self.delivered = 0         # in-order segments delivered
        self.out_of_order_count = 0

    def on_packet(self, seq):
        """Returns the cumulative ACK number (next expected segment)."""
        if seq == self.expected:
            self.expected += 1
            self.delivered += 1
            while self.expected in self.buffered:
                self.buffered.remove(self.expected)
                self.expected += 1
                self.delivered += 1
        elif seq > self.expected:
            self.buffered.add(seq)
            self.out_of_order_count += 1
        # seq < expected: duplicate of an old segment, just re-ACK.
        return self.expected
