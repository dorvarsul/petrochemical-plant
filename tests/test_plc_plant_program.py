"""The generated connected-plant PLC programs (one BPCS + one SIS, 3 instances).

These are the multi-unit programs that ``plc.load_scenario.load_plant`` loads
into the two OpenPLC slaves. Each is the single-unit template replicated once per
unit, with that unit's constants substituted and its located addresses shifted
into the unit's offset block (matching ``registers.unit_addr``). Pure text
checks — no OpenPLC needed (the live compile/load is exercised opt-in elsewhere).
"""

import re
import unittest

from common import registers as R
from common import scenarios
from plc.load_scenario import bpcs_plant_program, sis_plant_program


class TestStructure(unittest.TestCase):
    def setUp(self):
        self.bpcs = bpcs_plant_program()
        self.sis = sis_plant_program()

    def test_one_pou_and_instance_per_unit(self):
        for text in (self.bpcs, self.sis):
            for uid in scenarios.ids():
                self.assertIn(f"PROGRAM prog_{uid}", text)
                self.assertIn(f"PROGRAM inst_{uid} WITH task0 : prog_{uid};", text)
            # the original single-unit POU name must be gone
            self.assertNotIn("prog0", text)

    def test_single_configuration(self):
        for text in (self.bpcs, self.sis):
            self.assertEqual(text.count("CONFIGURATION Config0"), 1)
            self.assertEqual(text.count("END_CONFIGURATION"), 1)
            self.assertEqual(text.count("END_PROGRAM"), len(scenarios.ids()))


class TestLocatedAddressOffsets(unittest.TestCase):
    """Each unit's located vars must sit at that unit's offset block."""

    def _pou(self, text, uid):
        m = re.search(rf"PROGRAM prog_{uid}\b.*?END_PROGRAM", text, re.DOTALL)
        assert m, f"no POU for {uid}"
        return m.group(0)

    def _coil_of(self, qx):  # "%QX2.3" -> 2*8+3
        n, b = qx.split(".")
        return int(n[3:]) * 8 + int(b)

    def test_bpcs_pv_and_feed_and_manmode_offsets(self):
        text = bpcs_plant_program()
        for uid, u in R.UNIT_INDEX.items():
            pou = self._pou(text, uid)
            # %MW for PV (memory band) and %QW for FEED_CMD (output band)
            mw = int(re.search(r"pv\s+AT %MW(\d+)", pou).group(1))
            qw = int(re.search(r"feed_cmd\s+AT %QW(\d+)", pou).group(1))
            qx = re.search(r"man_mode\s+AT (%QX\d+\.\d+)", pou).group(1)
            self.assertEqual(R.MW_HOLDING_BASE + mw,
                             R.unit_addr("holding", int(R.BpcsHolding.PV), u))
            self.assertEqual(qw, R.unit_addr("holding", int(R.BpcsHolding.FEED_CMD), u))
            self.assertEqual(self._coil_of(qx),
                             R.unit_addr("coil", int(R.BpcsCoil.MAN_MODE), u))

    def test_sis_bypass_coil_offset(self):
        text = sis_plant_program()
        for uid, u in R.UNIT_INDEX.items():
            pou = self._pou(text, uid)
            qx = re.search(r"bypass_active\s+AT (%QX\d+\.\d+)", pou).group(1)
            self.assertEqual(self._coil_of(qx),
                             R.unit_addr("coil", int(R.SisCoil.BYPASS_ACTIVE), u))


class TestPerUnitConstants(unittest.TestCase):
    """Every unit's own gains / clamp / trip must be baked into its POU."""

    def _pou(self, text, uid):
        return re.search(rf"PROGRAM prog_{uid}\b.*?END_PROGRAM", text, re.DOTALL).group(0)

    def test_bpcs_gains_and_clamp_per_unit(self):
        text = bpcs_plant_program()
        for uid in scenarios.ids():
            sc = scenarios.get(uid)
            pou = self._pou(text, uid)
            self.assertRegex(pou, rf"BIAS_PM\s*:\s*DINT\s*:=\s*{sc.ctrl_bias_pm}\s*;")
            self.assertRegex(pou, rf"KP_NUM\s*:\s*DINT\s*:=\s*{sc.ctrl_kp_num}\s*;")
            self.assertRegex(pou, rf"SP_HI\s*:\s*INT\s*:=\s*{sc.sp_hi_reg}\s*;")

    def test_reactor_gain_is_negative(self):
        pou = self._pou(bpcs_plant_program(), "reactor")
        self.assertRegex(pou, r"KP_NUM\s*:\s*DINT\s*:=\s*-\d+\s*;")

    def test_sis_trip_per_unit(self):
        text = sis_plant_program()
        for uid in scenarios.ids():
            sc = scenarios.get(uid)
            pou = self._pou(text, uid)
            self.assertRegex(pou, rf"TRIP_HH\s*:\s*INT\s*:=\s*{sc.trip_reg}\s*;")


if __name__ == "__main__":
    unittest.main()
