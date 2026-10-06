"""Demo CLI: python3 -m tcp_sim.demo [scenario]

Scenarios: clean (default), timeout, fastrtx, lossy, reorder
"""

from __future__ import annotations

import sys

from .network import NetworkConfig
from .simulation import SimConfig, Simulation


def _scenario(name: str) -> SimConfig:
    if name == "clean":
        return SimConfig(total_packets=40, network=NetworkConfig(seed=1))
    if name == "timeout":
        # Last packet of the transfer is lost: nobody is left to generate
        # duplicate ACKs, so the RTO timer must fire.
        return SimConfig(total_packets=20,
                         network=NetworkConfig(seed=1, drop_seqs=frozenset({19})))
    if name == "fastrtx":
        # One mid-stream loss -> 3 dup ACKs -> fast retransmit + recovery.
        return SimConfig(total_packets=40,
                         network=NetworkConfig(seed=1, drop_seqs=frozenset({12})))
    if name == "lossy":
        return SimConfig(total_packets=60,
                         network=NetworkConfig(loss_rate=0.15, ack_loss_rate=0.05,
                                               seed=7))
    if name == "reorder":
        return SimConfig(total_packets=40,
                         network=NetworkConfig(reorder_rate=0.3,
                                               reorder_extra_delay=4.0, seed=3))
    raise SystemExit(f"unknown scenario {name!r}; "
                     f"choose from clean/timeout/fastrtx/lossy/reorder")


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 else "clean"
    sim = Simulation(_scenario(name))
    for entry in sim.run():
        print(entry.row())
    print(sim.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
