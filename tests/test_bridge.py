"""Integrated-loop tests for the Modbus bridge, using the fake PLCs.

These exercise the whole Phase-0 scenario logic — normal fill → SIS trip holds
it safe, vs. attack → overflow while the HMI stays frozen and "armed" — without
Docker or OpenPLC. They are the deterministic, dependency-free proof of the
demo logic and the register wiring (TESTING.md sections 3 and 5). The live
round-trip against real OpenPLC is tests/test_bridge_live.py.
"""

import unittest

from bridge.bridge import Bridge
from bridge.plc_io import FakeBpcs, FakeSis
from common import registers as R
from physics.tank import TankModel

DT = 0.1


def make_bridge():
    # Phase-0 tank-overfill scenario: batch fill (no draw), start mid-level.
    tank = TankModel(q_out=0.0, level0=5.0)
    return Bridge(tank, FakeBpcs(), FakeSis(), dt=DT)


class TestWiring(unittest.TestCase):
    def test_sensor_injected_into_both_plcs(self):
        b = make_bridge()
        b.step()
        reg = R.level_to_reg(5.0)  # the level that was published at step start
        self.assertEqual(b.bpcs.read_holding(R.BpcsHolding.LEVEL_PV), reg)
        self.assertEqual(b.sis.read_holding(R.SisHolding.LEVEL_PV), reg)

    def test_bpcs_holds_feed_open(self):
        b = make_bridge()
        s = b.step()
        self.assertEqual(b.bpcs.read_holding(R.BpcsHolding.FEED_CMD), R.VALVE_REG_MAX)
        self.assertAlmostEqual(s.feed, 1.0)

    def test_hmi_tracks_truth_when_not_attacked(self):
        b = make_bridge()
        for _ in range(50):
            s = b.step()
            self.assertLess(s.divergence, 0.05)  # within ~1 cycle of lag


class TestNormalRunStaysSafe(unittest.TestCase):
    def test_sis_trips_and_tank_never_overflows(self):
        b = make_bridge()
        tripped_seen = False
        max_level = 0.0
        for _ in range(2000):
            s = b.step()
            tripped_seen = tripped_seen or s.trip_cmd
            max_level = max(max_level, s.true_level)
            if s.trip_cmd and s.feed == 0.0 and b.tank.level < b.tank.height_full:
                pass
        self.assertTrue(tripped_seen, "SIS never tripped")
        self.assertFalse(b.tank.overflowing, "tank overflowed despite healthy SIS")
        self.assertLess(max_level, b.tank.height_full)
        # After a latched trip with no draw, the level holds just above 9 m.
        self.assertAlmostEqual(b.tank.level, 9.0, delta=0.1)
        self.assertTrue(b.hmi_trip_status(), "HMI should show tripped after a real trip")

    def test_feed_forced_zero_on_trip(self):
        b = make_bridge()
        b.tank.level = 9.5  # already above the trip threshold
        s = b.step()
        self.assertTrue(s.trip_cmd)
        self.assertEqual(s.feed, 0.0)


class TestAttack(unittest.TestCase):
    def test_overflow_while_hmi_frozen_and_armed(self):
        b = make_bridge()

        # Normal operation for a while; capture what the operator last saw.
        for _ in range(40):
            normal = b.step()
        self.assertFalse(normal.trip_cmd)
        frozen_hmi = normal.hmi_level
        self.assertAlmostEqual(frozen_hmi, b.tank.level, delta=0.05)

        # Inject the attack: force/bypass on the Safety PLC.
        b.sis.write_coil(R.SisCoil.OVERRIDE, True)

        overflowed = False
        for _ in range(3000):
            s = b.step()
            self.assertFalse(s.trip_cmd, "trip fired despite override")
            self.assertFalse(s.hmi_trip_status, "HMI left 'armed' state under attack")
            self.assertAlmostEqual(s.hmi_level, frozen_hmi, delta=1e-6,
                                   msg="HMI level was not frozen")
            if b.tank.overflowing:
                overflowed = True
                break

        self.assertTrue(overflowed, "attack did not drive the tank to overflow")
        # Acceptance F: a large, unmistakable gap between truth and the HMI.
        self.assertGreater(s.true_level - s.hmi_level, 4.0)

    def test_override_from_start_never_trips(self):
        b = make_bridge()
        b.sis.write_coil(R.SisCoil.OVERRIDE, True)
        for _ in range(3000):
            s = b.step()
            self.assertFalse(s.trip_cmd)
            if b.tank.overflowing:
                break
        self.assertTrue(b.tank.overflowing)


if __name__ == "__main__":
    unittest.main()
