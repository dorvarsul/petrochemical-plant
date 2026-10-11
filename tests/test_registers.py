"""Tests for the shared register map (common/registers.py).

Guards the single source of truth: valve scaling round-trips and clamps, and the
per-instance address-space / metadata invariants described in ARCHITECTURE.md.
Per-scenario PV scaling lives in common/scenarios.py and is covered by
test_scenarios.py.
"""

import unittest

from common import registers as R


class TestValveScaling(unittest.TestCase):
    def test_valve_round_trip(self):
        for f in (0.0, 0.4, 0.5, 1.0):
            self.assertAlmostEqual(R.reg_to_valve(R.valve_to_reg(f)), f, places=3)

    def test_valve_clamps_to_range(self):
        self.assertEqual(R.valve_to_reg(-2.0), 0)
        self.assertEqual(R.valve_to_reg(5.0), R.VALVE_REG_MAX)
        self.assertEqual(R.reg_to_valve(99999), 1.0)

    def test_valve_range_fits_u16(self):
        self.assertLessEqual(R.VALVE_REG_MAX, R.REG_U16_MAX)


class TestMapInvariants(unittest.TestCase):
    def test_every_register_has_info(self):
        keys = set(R.REGISTERS)
        expected = {
            (R.Instance.BPCS, "holding", R.BpcsHolding.PV),
            (R.Instance.BPCS, "holding", R.BpcsHolding.FEED_CMD),
            (R.Instance.SIS, "coil", R.SisCoil.TRIP_CMD),
            (R.Instance.SIS, "coil", R.SisCoil.TRIP_STATUS),
            (R.Instance.SIS, "coil", R.SisCoil.OVERRIDE),
            (R.Instance.SIS, "holding", R.SisHolding.PV),
            (R.Instance.SIS, "holding", R.SisHolding.PV_HMI),
        }
        self.assertEqual(keys, expected)

    def test_info_matches_key(self):
        for (inst, table, _addr), info in R.REGISTERS.items():
            self.assertEqual(info.instance, inst)
            self.assertEqual(info.table, table)

    def test_coils_addressed_from_zero(self):
        # Coils are a per-instance table addressed from 0 (independent slaves).
        self.assertEqual(min(R.SisCoil), 0)

    def test_holding_in_qw_or_mw_bands(self):
        # Holding addresses are either %QW (< 1024) or %MW (>= 1024); never negative.
        for enum_cls in (R.BpcsHolding, R.SisHolding):
            for m in enum_cls:
                self.assertGreaterEqual(int(m), 0)
                self.assertLessEqual(int(m), R.REG_U16_MAX)

    def test_addresses_unique_within_table(self):
        for enum_cls in (R.BpcsHolding, R.SisCoil, R.SisHolding):
            addrs = [int(m) for m in enum_cls]
            self.assertEqual(len(addrs), len(set(addrs)))

    def test_holding_mapping_matches_address(self):
        # OpenPLC %QWk -> holding register k; %MWk -> holding register 1024+k.
        for (_inst, table, addr), info in R.REGISTERS.items():
            if table != "holding":
                continue
            if info.st_var.startswith("%QW"):
                self.assertEqual(int(addr), int(info.st_var[3:]))
            elif info.st_var.startswith("%MW"):
                self.assertEqual(int(addr), R.MW_HOLDING_BASE + int(info.st_var[3:]))
            else:
                self.fail(f"unexpected holding st_var {info.st_var!r}")

    def test_sensor_is_memory_word_not_output(self):
        # The master->program sensor must be a %MW (shared memory), never a %QW
        # output (which the program image clobbers each scan).
        for inst, pv in ((R.Instance.BPCS, R.BpcsHolding.PV),
                         (R.Instance.SIS, R.SisHolding.PV)):
            info = R.REGISTERS[(inst, "holding", pv)]
            self.assertTrue(info.st_var.startswith("%MW"), info.st_var)
            self.assertGreaterEqual(int(pv), R.MW_HOLDING_BASE)

    def test_qx_mapping_matches_address(self):
        # OpenPLC %QXn.m -> coil n*8+m; coils live in byte 0.
        for (_inst, table, addr), info in R.REGISTERS.items():
            if table == "coil":
                self.assertEqual(info.st_var, f"%QX0.{int(addr)}")

    def test_endpoints_distinct(self):
        self.assertEqual(set(R.ENDPOINTS), set(R.Instance))
        ports = [p for _h, p in R.ENDPOINTS.values()]
        self.assertEqual(len(ports), len(set(ports)))


if __name__ == "__main__":
    unittest.main()
