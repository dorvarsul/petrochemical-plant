"""Physics unit tests for the vessel-overpressure model.

Mirrors the tank tests' spirit: the protective action (relief valve) holds the
process safe, the defeated protection runs it to failure, and the integrator is
well-behaved. Pure stdlib.
"""

import math
import unittest

from common import scenarios
from physics.vessel import VesselModel


class TestRiseToRupture(unittest.TestCase):
    """Compressor open, relief defeated (no trip) => monotonic rise to rupture."""

    def test_monotonic_rise_crosses_trip_and_ruptures(self):
        v = VesselModel()
        s = v.scenario
        prev = v.pressure
        crossed_trip = False
        for _ in range(1000):
            v.step(1.0, feed_cmd=1.0, trip=False)
            self.assertGreaterEqual(v.pressure + 1e-9, prev)  # non-decreasing
            prev = v.pressure
            crossed_trip = crossed_trip or v.pressure >= s.trip_hh
            if v.ruptured:
                break
        self.assertTrue(crossed_trip)
        self.assertTrue(v.ruptured)
        self.assertEqual(v.failed, v.ruptured)
        self.assertAlmostEqual(v.pressure, s.fail_at, places=6)

    def test_rupture_time_matches_analytic(self):
        v = VesselModel()
        s = v.scenario
        rate = v.k_in * 1.0 - v.k_out  # bar/s at full feed, no relief
        expected = (s.fail_at - s.pv0) / rate
        while not v.ruptured and v.time < 10000:
            v.step(1.0, feed_cmd=1.0, trip=False)
        self.assertAlmostEqual(v.time, expected, delta=1.5)


class TestTripHoldsSafe(unittest.TestCase):
    """Relief valve (trip) overpowers the feed => pressure falls, never ruptures."""

    def test_trip_from_start_vents_down(self):
        v = VesselModel()
        start = v.pressure
        for _ in range(500):
            v.step(1.0, feed_cmd=1.0, trip=True)
        self.assertLess(v.pressure, start)
        self.assertFalse(v.ruptured)
        self.assertEqual(v.pressure, 0.0)  # vents to the floor and pins

    def test_healthy_latch_arrests_before_rupture(self):
        # Rise under no trip until past the trip threshold, then assert relief.
        v = VesselModel()
        s = v.scenario
        while v.pressure < s.trip_hh:
            v.step(1.0, feed_cmd=1.0, trip=False)
        peak = v.pressure
        for _ in range(300):
            v.step(1.0, feed_cmd=1.0, trip=True)
        self.assertFalse(v.ruptured)
        self.assertLessEqual(v.pressure, peak)
        self.assertLess(peak, s.fail_at)


class TestActuatorAndSanity(unittest.TestCase):
    def test_applied_feed_tracks_command_and_clamps(self):
        v = VesselModel()
        v.step(1.0, feed_cmd=0.5, trip=False)
        self.assertAlmostEqual(v.applied_feed, 0.5)
        v.step(1.0, feed_cmd=5.0, trip=False)
        self.assertEqual(v.applied_feed, 1.0)
        v.step(1.0, feed_cmd=-2.0, trip=False)
        self.assertEqual(v.applied_feed, 0.0)

    def test_reset_restores_initial(self):
        v = VesselModel()
        for _ in range(50):
            v.step(1.0, feed_cmd=1.0, trip=False)
        v.reset()
        self.assertEqual(v.pressure, v.scenario.pv0)
        self.assertEqual(v.time, 0.0)
        self.assertFalse(v.ruptured)

    def test_no_nan_and_bad_dt(self):
        v = VesselModel()
        for _ in range(300):
            v.step(1.0, feed_cmd=1.0, trip=False)
            self.assertTrue(math.isfinite(v.pressure))
        with self.assertRaises(ValueError):
            v.step(0.0, feed_cmd=1.0)

    def test_fraction_matches_scenario(self):
        v = VesselModel()
        self.assertAlmostEqual(v.fraction, v.scenario.fraction(v.pressure))


if __name__ == "__main__":
    unittest.main()
