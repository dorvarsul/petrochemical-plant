"""Tests for the scenario registry (common/scenarios.py).

Guards the single source of per-scenario truth: integer Modbus scaling
round-trips and clamps, the register ranges fit a 16-bit Modbus register, and
the cross-scenario invariants the rest of the testbed relies on (failure ==
full scale, trip below failure, PV starts below the trip).
"""

import unittest

from common import scenarios

REG_CEILING = 65535


class TestRegistry(unittest.TestCase):
    def test_ids_present_and_ordered(self):
        self.assertEqual(scenarios.ids(), ["tank", "vessel", "reactor"])
        for sid in scenarios.ids():
            self.assertIn(sid, scenarios.SCENARIOS)

    def test_get_unknown_raises(self):
        with self.assertRaises(KeyError):
            scenarios.get("nope")

    def test_default_is_tank(self):
        self.assertEqual(scenarios.DEFAULT_SCENARIO, "tank")


class TestScalingAndRanges(unittest.TestCase):
    def test_reg_round_trip(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            for frac in (0.0, 0.1, 0.5, 0.9, 1.0):
                pv = frac * sc.pv_full_scale
                back = sc.reg_to_pv(sc.pv_to_reg(pv))
                self.assertAlmostEqual(back, pv, delta=1.0 / sc.reg_scale)

    def test_reg_clamps_to_range(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.pv_to_reg(-10.0), 0)
            self.assertEqual(sc.pv_to_reg(1e9), sc.reg_full_scale)

    def test_ranges_fit_u16(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertLessEqual(sc.reg_full_scale, REG_CEILING)
            self.assertLessEqual(sc.trip_reg, REG_CEILING)
            self.assertGreater(sc.reg_full_scale, 0)

    def test_fraction_clamped_0_1(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.fraction(-5.0), 0.0)
            self.assertEqual(sc.fraction(sc.fail_at * 2), 1.0)
            self.assertAlmostEqual(sc.fraction(sc.fail_at), 1.0)


class TestInvariants(unittest.TestCase):
    def test_full_scale_is_failure(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.pv_full_scale, sc.fail_at)

    def test_trip_strictly_between_start_and_failure(self):
        # The PV starts below the trip, and the trip is below failure, so a
        # healthy SIS has room to act before the process fails.
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertLess(sc.pv0, sc.trip_hh, sid)
            self.assertLess(sc.trip_hh, sc.fail_at, sid)

    def test_trip_reg_matches_scaling(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.trip_reg, round(sc.trip_hh * sc.reg_scale))


if __name__ == "__main__":
    unittest.main()
