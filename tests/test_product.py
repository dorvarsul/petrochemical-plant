"""Tests for the P-401 product-storage tank and the tank fill-valve gating.

Covers the new terminal-sink physics (physics/product.py), the block/fill-valve
gating added to physics/tank.py, and their wiring into the connected plant
(a tank SIS trip shuts the fill valve; the product level and export valve show
up in the plant snapshot).
"""

import unittest
from dataclasses import replace

from bridge.plant_bridge import make_fake_plant_bridge
from common import scenarios
from physics.product import ProductTankModel
from physics.tank import TankModel

DT = 1.0


class TestProductPhysics(unittest.TestCase):
    def test_export_open_holds_level(self):
        # q_export == q_fill -> net zero -> the level holds at its start.
        p = ProductTankModel()
        start = p.level
        for _ in range(200):
            p.step(DT, export_open=True)
        self.assertAlmostEqual(p.level, start, places=6)

    def test_export_closed_fills_to_hi_then_hh(self):
        p = ProductTankModel()
        reached_hi = reached_hh = None
        for i in range(1, 400):
            p.step(DT, export_open=False)
            if reached_hi is None and p.hi:
                reached_hi = i
            if reached_hh is None and p.hh:
                reached_hh = i
                break
        self.assertIsNotNone(reached_hi, "never reached HI with export shut")
        self.assertIsNotNone(reached_hh, "never reached HH with export shut")
        self.assertLess(reached_hi, reached_hh)
        self.assertLessEqual(p.level, p.spec.height_full)  # clamped at the brim

    def test_reduced_fill_drains_to_lo(self):
        # With no inflow and export open, the level drains and trips LO then LL.
        spec = replace(scenarios.PRODUCT, q_fill=0.0)
        p = ProductTankModel(spec=spec)
        for _ in range(400):
            p.step(DT, export_open=True)
            if p.ll:
                break
        self.assertTrue(p.lo)
        self.assertTrue(p.ll)
        self.assertGreaterEqual(p.level, 0.0)  # clamped at the floor

    def test_snapshot_shape(self):
        snap = ProductTankModel().snapshot()
        for k in ("tag", "pv", "pv_units", "fraction", "export_open",
                  "ll", "lo", "hi", "hh"):
            self.assertIn(k, snap)


class TestTankFillGating(unittest.TestCase):
    def test_closed_fill_valve_forces_feed_shut(self):
        t = TankModel()
        t.fill_open = False
        start = t.level
        t.step(DT, feed_cmd=1.0)  # valve wide open, but the block valve is shut
        self.assertLess(t.level, start, "feed should be blocked -> level drains")

    def test_default_fill_open_is_unchanged_behaviour(self):
        # Standalone default (fill_open True) matches a plain fed step.
        a = TankModel()
        b = TankModel()
        a.step(DT, feed_cmd=1.0)
        b.step(DT, feed_cmd=1.0)  # both default fill_open True
        self.assertEqual(a.level, b.level)
        self.assertTrue(TankModel().fill_open)


class TestPlantWiring(unittest.TestCase):
    def test_snapshot_exposes_product_and_fill(self):
        b = make_fake_plant_bridge(dt=0.1)
        s = b.step()
        self.assertIsNotNone(s.product)
        self.assertIn("pv", s.product)
        self.assertTrue(s.fill_open, "fill valve should be open at baseline")

    def test_tank_trip_shuts_the_fill_valve(self):
        # Manual drive on the tank, SIS armed: it trips, and the block-valve
        # final element shuts the fill (fill_open goes False).
        b = make_fake_plant_bridge(dt=0.1)
        s = None
        for i in range(4000):
            if i == 50:
                b.attack("tank", bypass=False)  # SIS left armed
            s = b.step()
            if s.units["tank"].trip_cmd and not s.fill_open:
                break
        self.assertTrue(s.units["tank"].trip_cmd, "tank SIS never tripped")
        self.assertFalse(s.fill_open, "trip did not shut the fill valve")

    def test_operator_can_close_export_valve(self):
        b = make_fake_plant_bridge(dt=0.1)
        b.set_export_valve(False)
        # takes effect on the next scan (OPV_OUT follows OPV_CMD)
        for _ in range(3):
            s = b.step()
        self.assertFalse(s.product["export_open"])


if __name__ == "__main__":
    unittest.main()
