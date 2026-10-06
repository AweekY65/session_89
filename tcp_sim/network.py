"""Deterministic in-memory network model.

Decides, for each packet/ACK, whether it is dropped and when it would be
delivered. Uses a seeded PRNG only — no real network, no real clock.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class NetworkConfig:
    one_way_delay: float = 10.0        # virtual time units, one direction
    loss_rate: float = 0.0             # probability a data packet is dropped
    ack_loss_rate: float = 0.0         # probability an ACK is dropped
    reorder_rate: float = 0.0          # probability a data packet is delayed
    reorder_extra_delay: float = 5.0   # extra delay applied to reordered packets
    seed: int = 0                      # PRNG seed -> fully deterministic runs
    drop_seqs: frozenset = frozenset()    # data seqs dropped deterministically
    drop_acks: frozenset = frozenset()    # ack numbers dropped deterministically

    @property
    def rtt(self) -> float:
        return 2.0 * self.one_way_delay


class Network:
    """Stateless-ish medium: given a send time, returns a delivery time."""

    def __init__(self, config: NetworkConfig):
        self.config = config
        self.rng = random.Random(config.seed)
        self.dropped_packets = 0
        self.dropped_acks = 0
        self.reordered = 0
        # Deterministic drops apply to the first transmission of a seq only,
        # so retransmissions can still get through.
        self._pending_drops = set(config.drop_seqs)

    def transmit_data(self, seq: int, now: float) -> float | None:
        """Return delivery time for a data packet, or None if lost."""
        deterministic_drop = seq in self._pending_drops
        self._pending_drops.discard(seq)
        if deterministic_drop or self.rng.random() < self.config.loss_rate:
            self.dropped_packets += 1
            return None
        delay = self.config.one_way_delay
        if self.rng.random() < self.config.reorder_rate:
            delay += self.config.reorder_extra_delay
            self.reordered += 1
        return now + delay

    def transmit_ack(self, ack_num: int, now: float) -> float | None:
        """Return delivery time for an ACK, or None if lost."""
        if ack_num in self.config.drop_acks or self.rng.random() < self.config.ack_loss_rate:
            self.dropped_acks += 1
            return None
        return now + self.config.one_way_delay
