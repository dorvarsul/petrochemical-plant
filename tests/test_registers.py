"""Tests for the shared register map (common/registers.py).

Guards the single source of truth: scaling round-trips, clamping at range
edges, and the owner-block / address-space invariants described in
ARCHITECTURE.md.
"""

import unittest

from common import registers as R


class TestScaling(unittest.TestCase):
    def test_level_round_trip(self):
        for m in (0.0, 0.123, 5.0, 9.0, 10.0):
            self.assertAlmostEqual(R.reg_to_level(R.level_to_reg(m)), m, places=3)

    def test_level_clamps_to_range(self):
        self.assertEqual(R.level_to_reg(-1.0), 0)
        self.assertEqual(R.level_to_reg(999.0), R.LEVEL_REG_MAX)
        self.assertLessEqual(R.level_to_reg(10.0), R.REG_U16_MAX)

    def test_valve_round_trip(self):
        for f in (0.0, 0.4, 0.5, 1.0):
            self.assertAlmostEqual(R.reg_to_valve(R.valve_to_reg(f)), f, places=3)

    def test_valve_clamps_to_range(self):
        self.assertEqual(R.valve_to_reg(-2.0), 0)
        self.assertEqual(R.valve_to_reg(5.0), R.VALVE_REG_MAX)
        self.assertEqual(R.reg_to_valve(99999), 1.0)


class TestMapInvariants(unittest.TestCase):
    def test_every_register_has_info(self):
        self.assertEqual(set(R.Coil), set(R.COIL_INFO))
        self.assertEqual(set(R.Holding), set(R.HOLDING_INFO))

    def test_owner_blocks(self):
        # BPCS holding registers in 0-9; SIS coils and holding regs in 10-19.
        self.assertTrue(all(0 <= a <= 9 for a, i in R.HOLDING_INFO.items() if i.owner == "BPCS"))
        self.assertTrue(all(10 <= a <= 19 for a, i in R.HOLDING_INFO.items() if i.owner == "SIS"))
        self.assertTrue(all(10 <= a <= 19 for a in R.Coil))

    def test_addresses_unique_within_space(self):
        self.assertEqual(len(list(R.Coil)), len(set(R.Coil)))
        self.assertEqual(len(list(R.Holding)), len(set(R.Holding)))

    def test_ranges_fit_u16(self):
        self.assertLessEqual(R.LEVEL_REG_MAX, R.REG_U16_MAX)
        self.assertLessEqual(R.VALVE_REG_MAX, R.REG_U16_MAX)


if __name__ == "__main__":
    unittest.main()
