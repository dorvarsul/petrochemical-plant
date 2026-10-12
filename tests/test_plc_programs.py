"""Tests for the OpenPLC ST programs and the scenario loader.

No Docker/OpenPLC needed: these check the ST source text against the single
sources of truth so the real PLCs cannot silently drift from common/registers.py
(located-variable addresses) or common/scenarios.py (the SIS trip threshold).
"""

import re
import unittest
from pathlib import Path

from common import registers as R
from common import scenarios
from plc.load_scenario import (BPCS_ST, SIS_TEMPLATE, bpcs_program_for,
                               sis_program_for)

TRIP_RE = re.compile(r"TRIP_HH\s*:\s*INT\s*:=\s*(\d+)\s*;")
BIAS_RE = re.compile(r"BIAS_PM\s*:\s*DINT\s*:=\s*(-?\d+)\s*;")
KPNUM_RE = re.compile(r"KP_NUM\s*:\s*DINT\s*:=\s*(-?\d+)\s*;")
KPDEN_RE = re.compile(r"KP_DEN\s*:\s*DINT\s*:=\s*(-?\d+)\s*;")
SPLO_RE = re.compile(r"SP_LO\s*:\s*INT\s*:=\s*(-?\d+)\s*;")
SPHI_RE = re.compile(r"SP_HI\s*:\s*INT\s*:=\s*(-?\d+)\s*;")
PREALARM_RE = re.compile(r"PRE_ALARM_H\s*:\s*INT\s*:=\s*(-?\d+)\s*;")
RESETDB_RE = re.compile(r"RESET_DB\s*:\s*INT\s*:=\s*(-?\d+)\s*;")
PVMAX_RE = re.compile(r"PV_MAX\s*:\s*INT\s*:=\s*(-?\d+)\s*;")


class TestBpcsProgram(unittest.TestCase):
    def setUp(self):
        self.text = Path(BPCS_ST).read_text()

    def test_runs_a_proportional_setpoint_loop(self):
        # The baseline program is a proportional controller holding PV at the
        # (clamped) writable setpoint, not a fixed wide-open valve.
        self.assertIn("feed_cmd := DINT_TO_INT(out_prev)", self.text)
        self.assertRegex(self.text, r"target\s*:=\s*BIAS_PM\s*\+")
        self.assertIn("sp_use - pv", self.text)
        self.assertNotIn("feed_cmd := 1000", self.text)

    def test_located_vars_match_register_map(self):
        # PV sensor / setpoint / manual output are %MW words; feed command %QW1;
        # mode + alarm + valve lines are coils.
        self.assertRegex(self.text, r"pv\s+AT %MW0")
        self.assertRegex(self.text, r"setpoint\s+AT %MW1")
        self.assertRegex(self.text, r"man_out\s+AT %MW2")
        self.assertRegex(self.text, r"feed_cmd\s+AT %QW1")
        self.assertRegex(self.text, r"man_mode\s+AT %QX0.0")
        self.assertRegex(self.text, r"opv_cmd\s+AT %QX0.4")
        self.assertEqual(int(R.BpcsHolding.PV), R.MW_HOLDING_BASE)
        self.assertEqual(int(R.BpcsHolding.SETPOINT), R.MW_HOLDING_BASE + 1)
        self.assertEqual(int(R.BpcsHolding.MAN_OUT), R.MW_HOLDING_BASE + 2)
        self.assertEqual(int(R.BpcsHolding.FEED_CMD), 1)

    def test_realism_logic_present(self):
        # The clamp, Auto/Manual branch, output rate limit, bumpless output
        # tracking, and the process alarms are all in the program text.
        self.assertIn("sp_clamped := (setpoint > SP_HI) OR (setpoint < SP_LO)",
                      self.text)
        self.assertIn("IF man_mode THEN", self.text)
        self.assertRegex(self.text, r"delta\s*:=\s*target\s*-\s*out_prev")
        self.assertIn("man_out := DINT_TO_INT(target)", self.text)  # bumpless
        self.assertIn("pv_hi_alm := pv >= SP_HI", self.text)
        self.assertIn("opv_out   := opv_cmd", self.text)

    def test_template_default_is_tank(self):
        # The committed default compiles standalone and equals the tank gains +
        # the tank setpoint-clamp bounds.
        tank = scenarios.get("tank")
        self.assertEqual(int(BIAS_RE.search(self.text).group(1)), tank.ctrl_bias_pm)
        self.assertEqual(int(KPNUM_RE.search(self.text).group(1)), tank.ctrl_kp_num)
        self.assertEqual(int(KPDEN_RE.search(self.text).group(1)), tank.ctrl_kp_den)
        self.assertEqual(int(SPLO_RE.search(self.text).group(1)), tank.sp_lo_reg)
        self.assertEqual(int(SPHI_RE.search(self.text).group(1)), tank.sp_hi_reg)


class TestSisTemplate(unittest.TestCase):
    def setUp(self):
        self.text = Path(SIS_TEMPLATE).read_text()

    def test_located_vars_match_register_map(self):
        self.assertRegex(self.text, r"pv\s+AT %MW0")  # sensor = shared memory word
        self.assertRegex(self.text, r"pv_hmi\s+AT %QW1")
        self.assertRegex(self.text, r"trip_cmd\s+AT %QX0.0")
        self.assertRegex(self.text, r"trip_status\s+AT %QX0.1")
        self.assertRegex(self.text, r"override\s+AT %QX0.2")
        self.assertRegex(self.text, r"reset_pb\s+AT %QX0.3")
        self.assertRegex(self.text, r"blk_out\s+AT %QX0.7")
        self.assertRegex(self.text, r"bypass_active AT %QX1.0")  # byte rollover
        self.assertEqual(int(R.SisHolding.PV), R.MW_HOLDING_BASE)
        self.assertEqual(int(R.SisHolding.PV_HMI), 1)
        self.assertEqual(int(R.SisCoil.TRIP_CMD), 0)
        self.assertEqual(int(R.SisCoil.OVERRIDE), 2)
        self.assertEqual(int(R.SisCoil.BLK_OUT), 7)
        self.assertEqual(int(R.SisCoil.BYPASS_ACTIVE), 8)

    def test_template_default_is_tank(self):
        # The committed default compiles standalone and equals the tank trip +
        # the tank pre-alarm / reset-deadband / full-scale.
        tank = scenarios.get("tank")
        m = TRIP_RE.search(self.text)
        self.assertIsNotNone(m, "template must keep a 'TRIP_HH : INT := <n>;' line")
        self.assertEqual(int(m.group(1)), tank.trip_reg)
        self.assertEqual(int(PREALARM_RE.search(self.text).group(1)),
                         tank.pre_alarm_h_reg)
        self.assertEqual(int(RESETDB_RE.search(self.text).group(1)),
                         tank.reset_db_reg)
        self.assertEqual(int(PVMAX_RE.search(self.text).group(1)),
                         tank.reg_full_scale)

    def test_override_suppresses_and_freezes(self):
        # The attack path must exist in the logic (bypass branch).
        self.assertIn("IF override THEN", self.text)
        self.assertIn("trip_cmd    := FALSE;", self.text)

    def test_realism_logic_present(self):
        # Pre-alarm, bad-PV fail-safe, deadband-gated reset, block-valve final
        # element and bypass annunciation are all in the program text.
        self.assertIn("pv_bad := (pv < PV_MIN) OR (pv > PV_MAX)", self.text)
        self.assertIn("prealarm_h := pv >= PRE_ALARM_H", self.text)
        self.assertIn("(pv >= TRIP_HH) OR pv_bad", self.text)
        self.assertIn("pv < (TRIP_HH - RESET_DB)", self.text)
        self.assertIn("blk_out := blk_open_cmd AND (NOT trip_cmd)", self.text)
        self.assertIn("bypass_active := override", self.text)


class TestScenarioSubstitution(unittest.TestCase):
    def test_each_scenario_gets_its_trip_reg(self):
        for sid in scenarios.ids():
            prog = sis_program_for(sid)
            m = TRIP_RE.search(prog)
            self.assertIsNotNone(m, sid)
            self.assertEqual(int(m.group(1)), scenarios.get(sid).trip_reg, sid)

    def test_each_scenario_gets_its_sis_constants(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            prog = sis_program_for(sid)
            self.assertEqual(int(PREALARM_RE.search(prog).group(1)),
                             sc.pre_alarm_h_reg, sid)
            self.assertEqual(int(RESETDB_RE.search(prog).group(1)),
                             sc.reset_db_reg, sid)
            self.assertEqual(int(PVMAX_RE.search(prog).group(1)),
                             sc.reg_full_scale, sid)

    def test_substitution_changes_only_the_constants(self):
        tank = sis_program_for("tank")
        reactor = sis_program_for("reactor")
        # Differ only on the four substituted constant lines.
        diff = [a for a, b in zip(tank.splitlines(), reactor.splitlines())
                if a != b]
        self.assertEqual(len(diff), 4)
        for line in diff:
            self.assertRegex(line, r"TRIP_HH|PRE_ALARM_H|RESET_DB|PV_MAX")

    def test_each_scenario_gets_its_bpcs_gains(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            prog = bpcs_program_for(sid)
            self.assertEqual(int(BIAS_RE.search(prog).group(1)), sc.ctrl_bias_pm, sid)
            self.assertEqual(int(KPNUM_RE.search(prog).group(1)), sc.ctrl_kp_num, sid)
            self.assertEqual(int(KPDEN_RE.search(prog).group(1)), sc.ctrl_kp_den, sid)

    def test_each_scenario_gets_its_setpoint_clamp(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            prog = bpcs_program_for(sid)
            self.assertEqual(int(SPLO_RE.search(prog).group(1)), sc.sp_lo_reg, sid)
            self.assertEqual(int(SPHI_RE.search(prog).group(1)), sc.sp_hi_reg, sid)

    def test_reactor_bpcs_gain_is_negative(self):
        # The reactor's actuator (coolant valve) removes energy, so its control
        # gain is signed negative — unique among the scenarios.
        self.assertLess(scenarios.get("reactor").ctrl_kp_num, 0)
        self.assertGreater(scenarios.get("tank").ctrl_kp_num, 0)
        self.assertGreater(scenarios.get("vessel").ctrl_kp_num, 0)

    def test_bpcs_substitution_changes_only_the_gain_lines(self):
        tank = bpcs_program_for("tank").splitlines()
        reactor = bpcs_program_for("reactor").splitlines()
        diff = [a for a, b in zip(tank, reactor) if a != b]
        # Exactly the five substituted constant lines differ: the three control
        # gains (bias/num/den) and the two setpoint-clamp bounds (lo/hi).
        self.assertEqual(len(diff), 5)
        for line in diff:
            self.assertRegex(line, r"BIAS_PM|KP_NUM|KP_DEN|SP_LO|SP_HI")


if __name__ == "__main__":
    unittest.main()
