"""Physics unit tests for the reactor-runaway model (2 coupled ODEs).

The reactor's BPCS actuator is the COOLANT VALVE: ``feed_cmd`` = valve fraction
``u``. u=0 (valve shut, the attack) must LOSE — the exotherm runs away; u=1
(valve full, normal max cooling) must WIN — safe; and the SIS trip (emergency
cooling, independent of u) must arrest the excursion well below failure. These
tests pin that control authority so a future coefficient change cannot silently
break the demo. Closed-loop baseline STABILITY (the controller holding 80 °C) is
covered by the integrated bridge tests, since it needs the control loop.
"""

import math
import unittest

from physics.reactor import ReactorModel


class TestRunawayWhenCoolantShut(unittest.TestCase):
    """Coolant valve shut (u=0, the attack) => the exotherm wins and T runs away."""

    def test_runs_away_to_failure(self):
        rx = ReactorModel()
        s = rx.scenario
        while not rx.runaway and rx.time < 2000:
            rx.step(1.0, feed_cmd=0.0, trip=False)
        self.assertTrue(rx.runaway)
        self.assertEqual(rx.failed, rx.runaway)
        self.assertAlmostEqual(rx.temperature, s.fail_at, places=6)
        # Legible timescale: a slow induction then an excursion, ~2-3 min.
        self.assertLess(rx.time, 300)
        self.assertGreater(rx.time, 90)

    def test_induction_then_excursion(self):
        # Temperature is monotonically non-decreasing on the way up, and the
        # late excursion is much faster than the early induction.
        rx = ReactorModel()
        t_at_trip = None
        prev = rx.temperature
        while not rx.runaway and rx.time < 2000:
            rx.step(1.0, feed_cmd=0.0, trip=False)
            self.assertGreaterEqual(rx.temperature + 1e-9, prev)
            prev = rx.temperature
            if t_at_trip is None and rx.temperature >= rx.scenario.trip_hh:
                t_at_trip = rx.time
        excursion = rx.time - t_at_trip
        self.assertGreater(t_at_trip, excursion)  # slow build, fast blow-up


class TestCoolingHoldsSafe(unittest.TestCase):
    """Coolant valve full and the SIS emergency trip both keep the reactor safe."""

    def test_full_coolant_from_start_stays_cold(self):
        rx = ReactorModel()
        s = rx.scenario
        for _ in range(600):
            rx.step(1.0, feed_cmd=1.0, trip=False)
            self.assertFalse(rx.runaway)
        self.assertLess(rx.temperature, s.trip_hh)

    def test_healthy_sis_latch_arrests_below_failure(self):
        # Coolant shut (attack drive) but the SIS armed: it must trip and arrest.
        rx = ReactorModel()
        s = rx.scenario
        tripped = False
        peak = rx.temperature
        for _ in range(1200):
            if rx.temperature >= s.trip_hh:
                tripped = True
            rx.step(1.0, feed_cmd=0.0, trip=tripped)
            peak = max(peak, rx.temperature)
            self.assertFalse(rx.runaway, "healthy SIS failed to prevent runaway")
        self.assertTrue(tripped, "trip threshold was never reached")
        # Arrested with comfortable margin below the failure threshold.
        self.assertLess(peak, s.fail_at - 50)


class TestKineticsAndSanity(unittest.TestCase):
    def test_concentration_stays_bounded(self):
        rx = ReactorModel()
        for _ in range(400):
            rx.step(1.0, feed_cmd=0.0, trip=False)
            self.assertGreaterEqual(rx.concentration, 0.0)
            self.assertLessEqual(rx.concentration, rx.C_in + 1e-9)
            if rx.runaway:
                break

    def test_rate_increases_with_temperature(self):
        rx = ReactorModel()
        r_lo = rx.rate(80.0, 0.6)
        r_hi = rx.rate(200.0, 0.6)
        self.assertGreater(r_hi, r_lo)  # Arrhenius: hotter => faster

    def test_applied_feed_clamps_and_reports_coolant(self):
        rx = ReactorModel()
        rx.step(1.0, feed_cmd=5.0, trip=False)
        self.assertEqual(rx.applied_feed, 1.0)
        rx.step(1.0, feed_cmd=-1.0, trip=False)
        self.assertEqual(rx.applied_feed, 0.0)
        # On an SIS trip the emergency cooling drives the jacket wide open.
        rx.step(1.0, feed_cmd=0.3, trip=True)
        self.assertEqual(rx.applied_feed, 1.0)

    def test_no_nan_and_bad_dt(self):
        rx = ReactorModel()
        for _ in range(300):
            rx.step(1.0, feed_cmd=0.0, trip=False)
            self.assertTrue(math.isfinite(rx.temperature))
            self.assertTrue(math.isfinite(rx.concentration))
            if rx.runaway:
                break
        with self.assertRaises(ValueError):
            rx.step(0.0, feed_cmd=0.0)

    def test_reset_restores_initial(self):
        rx = ReactorModel()
        for _ in range(100):
            rx.step(1.0, feed_cmd=0.0, trip=False)
        rx.reset()
        self.assertEqual(rx.temperature, rx.scenario.pv0)
        self.assertEqual(rx.concentration, rx.C0)
        self.assertFalse(rx.runaway)


if __name__ == "__main__":
    unittest.main()
