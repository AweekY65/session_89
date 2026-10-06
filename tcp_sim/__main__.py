"""Demo: ``python -m tcp_sim`` prints the event timeline of a sample run."""

from .simulation import Simulation


def print_timeline(sim, limit=None):
    print(f"{'time':>10} {'event':<34} {'cwnd':>8} {'ssthresh':>8} "
          f"{'flight':>6}  state")
    entries = sim.timeline if limit is None else sim.timeline[:limit]
    for e in entries:
        print(f"{e.time:>10.3f} {e.event:<34} {e.cwnd:>8.2f} "
              f"{e.ssthresh:>8.2f} {e.inflight:>6}  {e.state}")


def main():
    sim = Simulation(total_segments=40, rtt=1.0, loss=0.05, ack_loss=0.02,
                     reorder=0.1, seed=1, init_ssthresh=16)
    sim.run()
    print_timeline(sim)
    print()
    for key, value in sim.summary().items():
        print(f"{key:>22}: {value}")


if __name__ == "__main__":
    main()
