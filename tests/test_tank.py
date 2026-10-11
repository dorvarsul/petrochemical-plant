"""Physics unit tests for the tank-overfill model (TESTING.md section 1).

Validates the ground-truth model in isolation, before any Modbus/PLC layer:
conservation, known steady state, monotonic failure across the threshold, and
numerical sanity. Pure stdlib unittest — no external dependencies.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import math
import unittest

from physics.tank import TankModel


class TestConservation(unittest.TestCase):
    """No net flow => the conserved quantity (volume/level) does not change."""

    def test_closed_tank_conserves_volume(self):
        # Valve shut and no draw: nothing flows, level must be constant.
        tank = TankModel(q_out=0.0, level0=4.0)
        v0 = tank.volume
        for _ in range(1000):
            tank.step(dt=0.5, feed_valve=0.0)
        self.assertAlmostEqual(tank.volume, v0, places=9)
        self.assertAlmostEqual(tank.level, 4.0, places=9)
        self.assertEqual(tank.spilled_volume, 0.0)


class TestSteadyState(unittest.TestCase):
    """Balanced flows (Q_in == Q_out) => dL/dt = 0 => level holds at setpoint."""

    def test_balanced_flow_holds_level(self):
        tank = TankModel(level0=5.0)  # q_in_max=0.05, q_out=0.02
        balanced_valve = tank.q_out / tank.q_in_max  # 0.4 -> Q_in == Q_out
        for _ in range(2000):
            tank.step(dt=1.0, feed_valve=balanced_valve)
        self.assertAlmostEqual(tank.level, 5.0, places=6)
        self.assertFalse(tank.overflowing)

    def test_inflow_helper_matches_valve(self):
        tank = TankModel()
        self.assertAlmostEqual(tank.inflow(1.0), tank.q_in_max)
        self.assertAlmostEqual(tank.inflow(0.0), 0.0)
        self.assertAlmostEqual(tank.inflow(0.5), 0.5 * tank.q_in_max)


class TestOverfillFailure(unittest.TestCase):
    """Protection OFF (valve stuck open) => monotonic rise across the threshold."""

    def test_monotonic_rise_crosses_trip_and_overflows(self):
        tank = TankModel(level0=5.0)
        levels = [tank.level]
        crossed_trip = False
        for _ in range(400):
            tank.step(dt=1.0, feed_valve=1.0)
            levels.append(tank.level)
            if tank.level >= tank.level_trip_hh:
                crossed_trip = True
            if tank.overflowing:
                break

        # Strictly non-decreasing the whole way up.
        for a, b in zip(levels, levels[1:]):
            self.assertGreaterEqual(b + 1e-12, a)

        # True state crosses the SIS trip threshold and reaches overflow.
        self.assertTrue(crossed_trip, "level never reached the trip threshold")
        self.assertTrue(tank.overflowing, "tank never overflowed")
        self.assertAlmostEqual(tank.level, tank.height_full, places=9)

    def test_time_to_overflow_matches_analytic(self):
        # dL/dt = (q_in_max - q_out)/area; time to rise (H - L0) at full feed.
        tank = TankModel(level0=5.0)
        rate = (tank.q_in_max - tank.q_out) / tank.area  # 0.03 m/s
        expected = (tank.height_full - tank.level0) / rate  # ~166.7 s
        dt = 0.1
        steps = 0
        while not tank.overflowing and steps < 100000:
            tank.step(dt=dt, feed_valve=1.0)
            steps += 1
        self.assertFalse(tank.overflowing and steps >= 100000)
        self.assertAlmostEqual(tank.time, expected, delta=dt)

    def test_spill_accumulates_after_overflow(self):
        tank = TankModel(level0=9.99)
        for _ in range(50):
            tank.step(dt=1.0, feed_valve=1.0)
        self.assertTrue(tank.overflowing)
        self.assertGreater(tank.spilled_volume, 0.0)


class TestDraining(unittest.TestCase):
    """Feed below draw => level falls monotonically and pins at the floor."""

    def test_drains_and_clamps_at_zero(self):
        tank = TankModel(level0=1.0)  # valve shut, q_out=0.02 draws it down
        prev = tank.level
        for _ in range(500):
            tank.step(dt=1.0, feed_valve=0.0)
            self.assertLessEqual(tank.level, prev + 1e-12)
            prev = tank.level
        self.assertEqual(tank.level, 0.0)
        self.assertGreaterEqual(tank.level, 0.0)


class TestNumericalSanity(unittest.TestCase):
    """No NaN/Inf; halving the timestep does not change the trajectory."""

    def _level_after(self, dt, total_time, valve):
        tank = TankModel(level0=5.0)
        n = round(total_time / dt)
        for _ in range(n):
            tank.step(dt=dt, feed_valve=valve)
        return tank.level

    def test_timestep_invariance(self):
        # Linear ODE + explicit Euler is exact away from clamps: dt and dt/2 agree.
        total_time = 100.0  # stays below overflow, so no clamping
        coarse = self._level_after(1.0, total_time, valve=1.0)
        fine = self._level_after(0.5, total_time, valve=1.0)
        self.assertAlmostEqual(coarse, fine, places=9)
        self.assertLess(coarse, TankModel().height_full)  # no clamp in window

    def test_no_nan_or_inf(self):
        tank = TankModel()
        for _ in range(1000):
            tank.step(dt=1.0, feed_valve=1.0)
            self.assertTrue(math.isfinite(tank.level))
            self.assertTrue(math.isfinite(tank.spilled_volume))


class TestValveAndValidation(unittest.TestCase):
    """Valve command clamps to [0,1]; bad parameters are rejected."""

    def test_valve_command_is_clamped(self):
        over = TankModel(level0=5.0)
        exactly_open = TankModel(level0=5.0)
        for _ in range(50):
            over.step(dt=1.0, feed_valve=5.0)       # >1 behaves like 1
            exactly_open.step(dt=1.0, feed_valve=1.0)
        self.assertAlmostEqual(over.level, exactly_open.level, places=9)

        neg = TankModel(q_out=0.0, level0=3.0)
        for _ in range(50):
            neg.step(dt=1.0, feed_valve=-2.0)       # <0 behaves like 0
        self.assertAlmostEqual(neg.level, 3.0, places=9)

    def test_rejects_bad_params(self):
        with self.assertRaises(ValueError):
            TankModel(area=0.0)
        with self.assertRaises(ValueError):
            TankModel(height_full=-1.0)
        with self.assertRaises(ValueError):
            TankModel(q_in_max=-0.1)
        with self.assertRaises(ValueError):
            TankModel(level0=999.0)

    def test_rejects_bad_dt(self):
        tank = TankModel()
        with self.assertRaises(ValueError):
            tank.step(dt=0.0, feed_valve=1.0)


if __name__ == "__main__":
    unittest.main()
