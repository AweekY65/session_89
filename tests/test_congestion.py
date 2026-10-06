"""End-to-end tests for the deterministic congestion-control simulator.

Every test prints the key window changes; run with ``pytest -s`` to see them.
No real network, OS TCP state, or wall-clock sleeping is involved anywhere.
"""

import time

import pytest

from tcp_sim import Simulation, State, VirtualClock


def show(sim, *kinds):
    """Print timeline entries whose event mentions any of ``kinds``."""
    for e in sim.timeline:
        if not kinds or any(k in e.event for k in kinds):
            print(f"  t={e.time:8.3f} {e.event:<32} cwnd={e.cwnd:7.2f} "
                  f"ssthresh={e.ssthresh:6.2f} inflight={e.inflight:2d} {e.state}")


def events(sim, kind):
    return [e for e in sim.timeline if kind in e.event]


# ---------------------------------------------------------------------------
# No-loss growth: slow start then congestion avoidance
# ---------------------------------------------------------------------------

def test_no_loss_slow_start_then_congestion_avoidance():
    sim = Simulation(total_segments=32, rtt=1.0, init_ssthresh=8.0).run()
    print("\n-- no-loss growth (SS then CA) --")
    show(sim, "ACK", "ENTER")

    assert sim.completed
    assert sim.sender.retransmits == 0
    assert sim.sender.timeouts == 0

    # Slow start doubles cwnd each RTT: 1 -> 2 -> 4 -> 8.
    ss_cwnds = [e.cwnd for e in events(sim, "ACK") if e.time <= 3.0]
    assert ss_cwnds[:3] == [2.0, 3.0, 4.0]          # t=1, then t=2 burst
    assert 8.0 in ss_cwnds

    # Transition into congestion avoidance exactly at ssthresh.
    enter = events(sim, "ENTER_CONGESTION_AVOIDANCE")
    assert len(enter) == 1 and enter[0].cwnd == 8.0

    # Congestion avoidance: additive increase, ~+1 segment per RTT.
    ca = [e for e in events(sim, "ACK") if e.time > enter[0].time]
    by_rtt = {}
    for e in ca:
        by_rtt[e.time] = e.cwnd
    times = sorted(by_rtt)
    gains = [by_rtt[t2] - by_rtt[t1] for t1, t2 in zip(times, times[1:])]
    assert all(0.5 < g < 1.5 for g in gains), gains
    assert sim.throughput > 0


# ---------------------------------------------------------------------------
# Timeout loss
# ---------------------------------------------------------------------------

def test_timeout_retransmits_and_reenters_slow_start():
    # First transmission of segment 0 is dropped; cwnd=1 so no dup ACKs
    # can appear -> the only possible recovery is the RTO timer.
    sim = Simulation(total_segments=8, rtt=1.0, rto=4.0,
                     drop_packets={0}).run()
    print("\n-- timeout: cwnd -> 1, re-enter slow start --")
    show(sim, "TIMEOUT", "ENTER", "ACK")

    assert sim.completed
    assert sim.sender.timeouts == 1
    assert sim.sender.retransmits == 1

    timeout = events(sim, "TIMEOUT")[0]
    assert timeout.cwnd == 1.0                 # window collapsed
    assert timeout.ssthresh == 2.0             # max(cwnd/2, 2)
    assert timeout.state == State.SLOW_START.value
    assert events(sim, "ENTER_SLOW_START")     # explicit state transition


def test_timeout_backoff_on_total_blackout():
    # 100% loss: nothing ever gets through, RTO must double each timeout.
    sim = Simulation(total_segments=5, rtt=1.0, rto=2.0, loss=1.0)
    sim.run(max_time=100.0)
    print("\n-- blackout: exponential RTO backoff --")
    show(sim, "TIMEOUT")

    assert not sim.completed
    assert sim.sender.timeouts >= 4
    assert sim.timeout_next_rtos[:3] == [4.0, 8.0, 16.0]  # capped at base*16
    assert all(e.cwnd == 1.0 for e in events(sim, "TIMEOUT"))
    assert sim.sender.state is State.SLOW_START


# ---------------------------------------------------------------------------
# Duplicate ACKs / fast retransmit / fast recovery
# ---------------------------------------------------------------------------

def fast_retransmit_scenario():
    # cwnd=10 in CA; segment 0's first transmission is lost, so segments
    # 1..9 arrive out of order and each triggers one duplicate ACK.
    return Simulation(total_segments=20, rtt=1.0, init_cwnd=10.0,
                      init_ssthresh=8.0, drop_packets={0}).run()


def test_duplicate_acks_counted_before_fast_retransmit():
    sim = fast_retransmit_scenario()
    print("\n-- dup ACKs counted: 1, 2, then fast retransmit on 3rd --")
    show(sim, "DUP_ACK", "FAST_RETRANSMIT")

    dups = events(sim, "DUP_ACK")
    assert [e.event for e in dups[:3]] == ["DUP_ACK n=1", "DUP_ACK n=2",
                                           "DUP_ACK n=3"]
    fr_index = next(i for i, e in enumerate(sim.timeline)
                    if "FAST_RETRANSMIT" in e.event)
    # No fast retransmit before the third duplicate ACK.
    assert all("DUP_ACK" in sim.timeline[i].event
               for i in range(fr_index - 1, fr_index - 4, -1))
    assert sim.sender.retransmits == 1
    assert sim.sender.timeouts == 0


def test_fast_retransmit_and_recovery_window_math():
    sim = fast_retransmit_scenario()
    print("\n-- fast retransmit / fast recovery window math --")
    show(sim, "FAST_RETRANSMIT", "INFLATE", "EXIT")

    fr = events(sim, "FAST_RETRANSMIT")[0]
    assert fr.ssthresh == 5.0            # max(10/2, 2)
    assert fr.cwnd == 8.0                # ssthresh + 3 (inflated)
    assert fr.state == State.FAST_RECOVERY.value

    # Each further dup ACK inflates cwnd by 1: 6 more dup ACKs -> 14.
    inflates = events(sim, "INFLATE")
    assert len(inflates) == 6
    assert inflates[-1].cwnd == 14.0

    # The new ACK covering the retransmit deflates cwnd to ssthresh.
    exit_fr = events(sim, "EXIT_FAST_RECOVERY")[0]
    assert exit_fr.cwnd == 5.0
    assert exit_fr.state == State.CONGESTION_AVOIDANCE.value

    assert sim.completed
    assert sim.sender.timeouts == 0      # recovered without any RTO


# ---------------------------------------------------------------------------
# Harsh but probabilistic networks
# ---------------------------------------------------------------------------

def test_high_loss_still_completes_with_many_retransmits():
    sim = Simulation(total_segments=40, rtt=1.0, loss=0.3, seed=42).run()
    print("\n-- 30% packet loss --")
    show(sim, "TIMEOUT", "FAST_RETRANSMIT", "ENTER")
    print("  summary:", sim.summary())

    assert sim.completed
    assert sim.sender.retransmits >= 3
    assert sim.network.stats["packets_dropped"] > 0


def test_reordering_completes_and_is_deterministic():
    def run_once():
        return Simulation(total_segments=30, rtt=1.0, reorder=0.6,
                          seed=7, init_cwnd=4.0).run()

    sim1, sim2 = run_once(), run_once()
    print("\n-- 60% reorder probability --")
    show(sim1, "DUP_ACK", "FAST_RETRANSMIT", "TIMEOUT")
    print("  summary:", sim1.summary())

    assert sim1.completed
    assert sim1.receiver.out_of_order_count > 0   # reordering really happened
    trace1 = [(e.time, e.event, e.cwnd, e.state) for e in sim1.timeline]
    trace2 = [(e.time, e.event, e.cwnd, e.state) for e in sim2.timeline]
    assert trace1 == trace2                       # fixed input -> fixed output


def test_ack_loss_only_recovers_via_remaining_acks():
    # The ACK for segment 0 is lost, but the cumulative ACK for segment 1
    # covers both, so no timeout and no retransmit should ever happen.
    sim = Simulation(total_segments=8, rtt=1.0, init_cwnd=2.0,
                     drop_acks={1}).run()
    print("\n-- ACK loss healed by cumulative ACKs --")
    show(sim, "ACK", "TIMEOUT")

    assert sim.completed
    assert sim.network.stats["acks_dropped"] == 1
    assert sim.sender.timeouts == 0
    assert sim.sender.retransmits == 0
    first_ack = events(sim, "ACK")[0]
    assert first_ack.event == "ACK ack=2 newly=2"   # cumulative cover


# ---------------------------------------------------------------------------
# Virtual clock behaviour
# ---------------------------------------------------------------------------

def test_virtual_clock_zero_delay_and_monotonicity():
    clock = VirtualClock()
    times = []
    clock.schedule(0.0, lambda: times.append(clock.now))
    clock.schedule(5.0, lambda: times.append(clock.now))
    clock.schedule(0.0, lambda: times.append(clock.now))
    clock.schedule(2.5, lambda: times.append(clock.now))
    while clock.run_next():
        pass
    print(f"\n-- zero-delay events, times={times} --")
    assert times == sorted(times)          # monotonic non-decreasing
    assert times == [0.0, 0.0, 2.5, 5.0]
    assert clock.now == 5.0
    with pytest.raises(ValueError):
        clock.schedule(-1.0, lambda: None)


def test_virtual_clock_fifo_at_identical_timestamps():
    clock = VirtualClock()
    order = []
    for i in range(5):
        clock.schedule(1.0, lambda i=i: order.append(i))
    for i in range(5, 8):
        clock.schedule(0.0, lambda i=i: order.append(i))
    while clock.run_next():
        pass
    print(f"\n-- FIFO at identical timestamps, order={order} --")
    # t=0 events (5,6,7) run first in insertion order, then t=1 events.
    assert order == [5, 6, 7, 0, 1, 2, 3, 4]


def test_simulation_uses_no_wall_clock(monkeypatch):
    # Any attempt to sleep for real would explode the runtime of a long
    # virtual simulation; forbid it outright and prove the run is fast.
    def forbidden_sleep(_seconds):
        raise AssertionError("simulation must not use the wall clock")

    monkeypatch.setattr(time, "sleep", forbidden_sleep)
    wall_start = time.perf_counter()
    sim = Simulation(total_segments=100, rtt=100.0, loss=0.05, seed=3).run()
    wall_elapsed = time.perf_counter() - wall_start
    print(f"\n-- virtual={sim.finish_time:.0f} time units simulated in "
          f"{wall_elapsed:.3f}s wall clock --")

    assert sim.completed
    assert sim.finish_time > 1000.0        # huge virtual time...
    assert wall_elapsed < 5.0              # ...tiny real time
