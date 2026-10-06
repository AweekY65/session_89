"""Terminal-runnable tests for the deterministic TCP congestion simulator.

Every scenario runs entirely in memory on a virtual clock — no real
network, no real sleep. Each test prints the key window transitions it
asserts on, and fixed seeds/configs make every run byte-for-byte
reproducible.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import time
import unittest

from tcp_sim import NetworkConfig, SimConfig, Simulation, State


def print_transitions(title: str, sim: Simulation, limit: int = 40) -> None:
    print(f"\n--- {title} ---")
    interesting = [
        e for e in sim.log
        if any(k in e.event for k in (
            "init", "timeout", "fast retransmit", "FAST_RECOVERY",
            "CONGESTION_AVOIDANCE", "done", "aborted",
        ))
    ]
    for entry in interesting[:limit]:
        print("  " + entry.row())
    print("  " + sim.summary())


def states_of(sim: Simulation) -> list[str]:
    return [e.state for e in sim.log]


class TestNoLossGrowth(unittest.TestCase):
    def test_slow_start_then_congestion_avoidance(self):
        sim = Simulation(SimConfig(total_packets=40,
                                   network=NetworkConfig(seed=1)))
        sim.run()
        print_transitions("no-loss growth", sim)

        self.assertTrue(sim.sender.done)
        self.assertEqual(sim.sender.retransmits, 0)
        states = states_of(sim)
        self.assertIn(State.SLOW_START.value, states)
        self.assertIn(State.CONGESTION_AVOIDANCE.value, states)
        # Slow start: +1 per ACK, doubling the window every RTT.
        cwnds = [e.cwnd for e in sim.log if "cwnd +=" in e.event]
        self.assertEqual(cwnds[:4], [2.0, 3.0, 4.0, 5.0])
        # +1 per ACK; the window doubles from one RTT to the next
        # (1 in flight, then 2, 4, 8, 16).
        self.assertEqual(cwnds[6], 8.0)
        self.assertIn(16.0, cwnds)        # grows up to the initial ssthresh
        # Entered CA exactly when cwnd reached the initial ssthresh.
        ca_entries = [e for e in sim.log if e.state == "CONGESTION_AVOIDANCE"]
        self.assertAlmostEqual(ca_entries[0].ssthresh, 16.0)
        # Window never decreased without loss.
        cwnd_seq = [e.cwnd for e in sim.log]
        self.assertEqual(cwnd_seq, sorted(cwnd_seq))


class TestTimeout(unittest.TestCase):
    def test_timeout_triggers_window_collapse_and_slow_start(self):
        # The final packet is lost: no dup ACKs possible, RTO must fire.
        sim = Simulation(SimConfig(
            total_packets=20,
            network=NetworkConfig(seed=1, drop_seqs=frozenset({19})),
        ))
        sim.run()
        print_transitions("timeout loss", sim)

        timeouts = [e for e in sim.log if "timeout" in e.event]
        self.assertEqual(len(timeouts), 1)
        self.assertEqual(timeouts[0].cwnd, 1.0)
        self.assertEqual(timeouts[0].state, State.SLOW_START.value)
        # ssthresh = max(cwnd/2, 2) at the moment of the timeout.
        idx = sim.log.index(timeouts[0])
        pre_cwnd = sim.log[idx - 1].cwnd
        self.assertAlmostEqual(timeouts[0].ssthresh, max(pre_cwnd / 2.0, 2.0))
        self.assertEqual(sim.sender.retransmits, 1)
        self.assertTrue(sim.sender.done)

    def test_timeout_can_enter_specified_recovery_state(self):
        sim = Simulation(SimConfig(
            total_packets=20,
            timeout_recovery_state=State.CONGESTION_AVOIDANCE,
            network=NetworkConfig(seed=1, drop_seqs=frozenset({19})),
        ))
        sim.run()
        print_transitions("timeout -> congestion avoidance", sim)
        timeouts = [e for e in sim.log if "timeout" in e.event]
        self.assertEqual(timeouts[0].state, State.CONGESTION_AVOIDANCE.value)
        self.assertTrue(sim.sender.done)


class TestDuplicateAcksAndFastRetransmit(unittest.TestCase):
    def test_three_dup_acks_trigger_fast_retransmit_and_recovery(self):
        sim = Simulation(SimConfig(
            total_packets=40,
            network=NetworkConfig(seed=1, drop_seqs=frozenset({12})),
        ))
        sim.run()
        print_transitions("fast retransmit / fast recovery", sim)

        dup = [e for e in sim.log if "dup ACK" in e.event]
        self.assertGreaterEqual(len(dup), 3)

        fr = [e for e in sim.log if "fast retransmit" in e.event]
        self.assertEqual(len(fr), 1)
        # Reno: ssthresh = cwnd/2, cwnd = ssthresh + 3 on entry.
        self.assertAlmostEqual(fr[0].ssthresh, 6.5)
        self.assertAlmostEqual(fr[0].cwnd, 9.5)
        self.assertEqual(fr[0].state, State.FAST_RECOVERY.value)

        # Inflation on further dup ACKs, then deflate to ssthresh.
        inflated = [e for e in sim.log if "inflate" in e.event]
        self.assertTrue(inflated)
        self.assertAlmostEqual(inflated[-1].cwnd, 9.5 + len(inflated))
        deflate = [e for e in sim.log if "deflate" in e.event]
        self.assertEqual(len(deflate), 1)
        self.assertAlmostEqual(deflate[0].cwnd, deflate[0].ssthresh)
        self.assertEqual(deflate[0].state, State.CONGESTION_AVOIDANCE.value)

        self.assertEqual(sim.sender.retransmits, 1)
        self.assertTrue(sim.sender.done)


class TestHighLoss(unittest.TestCase):
    def test_high_loss_keeps_window_small_and_completes(self):
        cfg = SimConfig(total_packets=60,
                        network=NetworkConfig(loss_rate=0.15,
                                              ack_loss_rate=0.05, seed=7))
        sim = Simulation(cfg)
        sim.run()
        print_transitions("high loss", sim)

        self.assertTrue(sim.sender.done)
        self.assertGreater(sim.sender.retransmits, 5)
        self.assertGreater(sim.network.dropped_packets, 5)
        # cwnd is repeatedly reset: it must stay well below the no-loss peak.
        peak = max(e.cwnd for e in sim.log)
        self.assertLess(peak, 17.0)
        timeouts = [e for e in sim.log if "timeout" in e.event]
        self.assertTrue(timeouts, "high loss should produce timeouts")

    def test_determinism_same_seed_same_trace(self):
        def signature():
            sim = Simulation(SimConfig(
                total_packets=60,
                network=NetworkConfig(loss_rate=0.15, ack_loss_rate=0.05,
                                      reorder_rate=0.1, seed=42)))
            return [(e.time, e.event, e.cwnd, e.state) for e in sim.run()]

        self.assertEqual(signature(), signature())


class TestReorder(unittest.TestCase):
    def test_reorder_generates_dup_acks_but_recovers(self):
        sim = Simulation(SimConfig(
            total_packets=40,
            network=NetworkConfig(reorder_rate=0.3, reorder_extra_delay=4.0,
                                  seed=3)))
        sim.run()
        print_transitions("reorder", sim)

        self.assertTrue(sim.sender.done)
        self.assertGreater(sim.network.reordered, 0)
        self.assertGreater(sim.network.dropped_packets, -1)  # none dropped
        self.assertEqual(sim.network.dropped_packets, 0)
        dup = [e for e in sim.log if "dup ACK" in e.event]
        self.assertTrue(dup, "reordering should produce duplicate ACKs")


class TestVirtualClock(unittest.TestCase):
    def test_zero_rtt_boundary_and_no_real_sleep(self):
        # Zero-delay network: every event shares timestamp 0; ordering must
        # still be deterministic and the run must not burn wall-clock time.
        cfg = SimConfig(total_packets=30,
                        network=NetworkConfig(one_way_delay=0.0, seed=5))
        sim = Simulation(cfg)
        start = time.perf_counter()
        sim.run()
        wall = time.perf_counter() - start
        print_transitions("virtual clock boundary (zero RTT)", sim)

        self.assertTrue(sim.sender.done)
        self.assertLess(wall, 1.0, "virtual clock must not use real sleep")
        self.assertEqual(sim.now, 0.0, "no virtual time should elapse")
        # Same-timestamp events are replayed identically on a second run.
        again = Simulation(cfg)
        again.run()
        self.assertEqual([(e.time, e.event, e.cwnd) for e in sim.log],
                         [(e.time, e.event, e.cwnd) for e in again.log])

    def test_rto_uses_virtual_rtt(self):
        # RTT = 2 * one_way_delay = 20; RTO = 2 * RTT = 40 (virtual units).
        sim = Simulation(SimConfig(
            total_packets=5,
            network=NetworkConfig(one_way_delay=10.0, seed=1,
                                  drop_seqs=frozenset({4}))))
        sim.run()
        print_transitions("RTO boundary", sim)
        timeouts = [e for e in sim.log if "timeout" in e.event]
        self.assertEqual(len(timeouts), 1)
        # Packet 4 is sent at t=60 (third slow-start round); the RTO timer
        # armed at that send fires exactly RTO=40 virtual units later.
        self.assertAlmostEqual(timeouts[0].time, 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
