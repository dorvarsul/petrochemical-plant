"""Integrated-loop tests for the Modbus bridge, using the fake PLCs.

These exercise the whole scenario logic — normal operation → SIS trip holds it
safe, vs. attack → failure while the HMI stays frozen and "armed" — without
Docker or OpenPLC, for ALL THREE scenarios. They are the deterministic,
dependency-free proof of the demo logic and the register wiring (TESTING.md
sections 3 and 5). The live round-trip against real OpenPLC is
tests/test_bridge_live.py.
"""

import unittest

from bridge.bridge import Bridge
from bridge.plc_io import FakeBpcs, FakeSis
from common import registers as R
from common import scenarios
from physics import make_model

DT = 0.1
ALL = scenarios.ids()


def make_bridge(sid):
    sc = scenarios.get(sid)
    return Bridge(make_model(sid), FakeBpcs(), FakeSis(sc.trip_reg), dt=DT)


class TestWiring(unittest.TestCase):
    def test_sensor_injected_into_both_plcs(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                b.step()
                reg = b.scenario.pv_to_reg(b.scenario.pv0)  # published at step start
                self.assertEqual(b.bpcs.read_holding(R.BpcsHolding.PV), reg)
                self.assertEqual(b.sis.read_holding(R.SisHolding.PV), reg)

    def test_bpcs_holds_feed_open(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                s = b.step()
                self.assertEqual(b.bpcs.read_holding(R.BpcsHolding.FEED_CMD),
                                 R.VALVE_REG_MAX)
                self.assertAlmostEqual(s.feed, 1.0)

    def test_hmi_tracks_truth_when_not_attacked(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                for _ in range(50):
                    s = b.step()
                    self.assertLess(s.divergence, 0.02 * b.scenario.fail_at)


class TestNormalRunStaysSafe(unittest.TestCase):
    def test_sis_trips_and_process_never_fails(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                tripped_seen = False
                max_pv = 0.0
                for _ in range(6000):
                    s = b.step()
                    tripped_seen = tripped_seen or s.trip_cmd
                    max_pv = max(max_pv, s.true_pv)
                    if b.model.failed:
                        break
                self.assertTrue(tripped_seen, f"{sid}: SIS never tripped")
                self.assertFalse(b.model.failed,
                                 f"{sid}: process failed despite healthy SIS")
                self.assertLess(max_pv, b.scenario.fail_at)
                self.assertTrue(b.hmi_trip_status(),
                                f"{sid}: HMI should show tripped after a real trip")

    def test_tank_feed_forced_zero_on_trip(self):
        # Tank-specific: the trip's protective action IS shutting the feed.
        b = make_bridge("tank")
        b.model.level = 9.5  # already above the trip threshold
        s = b.step()
        self.assertTrue(s.trip_cmd)
        self.assertEqual(s.feed, 0.0)


class TestAttack(unittest.TestCase):
    def test_failure_while_hmi_frozen_and_armed(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                sc = b.scenario

                # Normal operation for a while; capture what the operator saw.
                for _ in range(40):
                    normal = b.step()
                self.assertFalse(normal.trip_cmd)
                frozen_hmi = normal.hmi_pv
                self.assertAlmostEqual(frozen_hmi, b.model.pv,
                                       delta=0.02 * sc.fail_at)

                # Inject the attack: force/bypass on the Safety PLC.
                b.sis.write_coil(R.SisCoil.OVERRIDE, True)

                failed = False
                for _ in range(8000):
                    s = b.step()
                    self.assertFalse(s.trip_cmd, f"{sid}: trip fired despite override")
                    self.assertFalse(s.hmi_trip_status,
                                     f"{sid}: HMI left 'armed' under attack")
                    self.assertAlmostEqual(s.hmi_pv, frozen_hmi, delta=1e-6,
                                           msg=f"{sid}: HMI PV was not frozen")
                    if b.model.failed:
                        failed = True
                        break
                self.assertTrue(failed, f"{sid}: attack did not drive to failure")
                # Acceptance F: a large, unmistakable gap between truth and HMI.
                self.assertGreater(s.true_pv - s.hmi_pv, 0.4 * sc.fail_at)

    def test_override_from_start_never_trips(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                b.sis.write_coil(R.SisCoil.OVERRIDE, True)
                for _ in range(8000):
                    s = b.step()
                    self.assertFalse(s.trip_cmd)
                    if b.model.failed:
                        break
                self.assertTrue(b.model.failed)


if __name__ == "__main__":
    unittest.main()
