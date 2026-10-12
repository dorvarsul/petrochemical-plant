"""Per-unit Modbus addressing for the connected whole-plant LIVE path.

The single-unit register map (test_registers.py) is the canonical block; here we
check the ``unit_addr`` projection that places three units (tank/reactor/vessel)
in their own offset blocks on one shared BPCS slave and one shared SIS slave, so
they never collide. The generated multi-unit ST programs use the same strides
(verified in test_plc_plant_program.py).
"""

import unittest

from common import registers as R


# Every single-unit address a unit actually uses, grouped by Modbus table.
_BPCS_COILS = list(R.BpcsCoil)
_SIS_COILS = list(R.SisCoil)
_BPCS_HOLD = list(R.BpcsHolding)
_SIS_HOLD = list(R.SisHolding)


class TestUnitIndex(unittest.TestCase):
    def test_three_units_matching_flow_order(self):
        self.assertEqual(set(R.UNIT_INDEX), {"tank", "reactor", "vessel"})
        self.assertEqual(sorted(R.UNIT_INDEX.values()), [0, 1, 2])

    def test_flow_order_matches_physics(self):
        from physics.plant import FLOW
        self.assertEqual(tuple(R.UNIT_INDEX[u] for u in FLOW), (0, 1, 2))


class TestUnitAddr(unittest.TestCase):
    def test_unit_zero_is_identity(self):
        # Unit 0 (tank) must map to the base address unchanged, so the
        # single-unit map stays canonical and the single-unit tests still hold.
        for a in _BPCS_COILS + _SIS_COILS:
            self.assertEqual(R.unit_addr("coil", int(a), 0), int(a))
        for a in _BPCS_HOLD + _SIS_HOLD:
            self.assertEqual(R.unit_addr("holding", int(a), 0), int(a))

    def test_coil_stride(self):
        self.assertEqual(R.unit_addr("coil", 0, 1), R.COIL_STRIDE)
        self.assertEqual(R.unit_addr("coil", 2, 2), 2 + 2 * R.COIL_STRIDE)

    def test_mw_band_uses_mw_stride(self):
        pv = int(R.BpcsHolding.PV)  # %MW0 -> holding 1024
        self.assertGreaterEqual(pv, R.MW_HOLDING_BASE)
        self.assertEqual(R.unit_addr("holding", pv, 1), pv + R.MW_STRIDE)
        self.assertEqual(R.unit_addr("holding", pv, 2), pv + 2 * R.MW_STRIDE)

    def test_qw_band_uses_qw_stride(self):
        feed = int(R.BpcsHolding.FEED_CMD)  # %QW1 -> holding 1 (below MW base)
        self.assertLess(feed, R.MW_HOLDING_BASE)
        self.assertEqual(R.unit_addr("holding", feed, 1), feed + R.QW_STRIDE)
        self.assertEqual(R.unit_addr("holding", feed, 2), feed + 2 * R.QW_STRIDE)


class TestNoCollisions(unittest.TestCase):
    """Across all three units, no two used addresses collide within a table."""

    def _projected(self, table, addrs):
        return [R.unit_addr(table, int(a), u)
                for u in R.UNIT_INDEX.values() for a in addrs]

    def test_bpcs_coils_distinct_across_units(self):
        all_ = self._projected("coil", _BPCS_COILS)
        self.assertEqual(len(all_), len(set(all_)))

    def test_sis_coils_distinct_across_units(self):
        all_ = self._projected("coil", _SIS_COILS)
        self.assertEqual(len(all_), len(set(all_)))

    def test_bpcs_holding_distinct_across_units(self):
        all_ = self._projected("holding", _BPCS_HOLD)
        self.assertEqual(len(all_), len(set(all_)))

    def test_sis_holding_distinct_across_units(self):
        all_ = self._projected("holding", _SIS_HOLD)
        self.assertEqual(len(all_), len(set(all_)))

    def test_sis_coil_blocks_do_not_overlap(self):
        # SIS uses 9 coils (0..8, rolling into byte 1); the stride must leave
        # each unit's block clear of the next unit's.
        top = max(int(a) for a in _SIS_COILS)
        self.assertLess(top, R.COIL_STRIDE,
                        "COIL_STRIDE too small: SIS blocks would overlap")


if __name__ == "__main__":
    unittest.main()
