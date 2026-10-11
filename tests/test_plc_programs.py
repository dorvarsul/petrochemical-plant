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
from plc.load_scenario import BPCS_ST, SIS_TEMPLATE, sis_program_for

TRIP_RE = re.compile(r"TRIP_HH\s*:\s*INT\s*:=\s*(\d+)\s*;")


class TestBpcsProgram(unittest.TestCase):
    def setUp(self):
        self.text = Path(BPCS_ST).read_text()

    def test_drives_feed_fully_open(self):
        self.assertIn("feed_cmd := 1000", self.text)

    def test_located_vars_match_register_map(self):
        # PV sensor is a %MW memory word (holding 1024); feed command is %QW1.
        self.assertRegex(self.text, r"pv\s+AT %MW0")
        self.assertRegex(self.text, r"feed_cmd AT %QW1")
        self.assertEqual(int(R.BpcsHolding.PV), R.MW_HOLDING_BASE)
        self.assertEqual(int(R.BpcsHolding.FEED_CMD), 1)


class TestSisTemplate(unittest.TestCase):
    def setUp(self):
        self.text = Path(SIS_TEMPLATE).read_text()

    def test_located_vars_match_register_map(self):
        self.assertRegex(self.text, r"pv\s+AT %MW0")  # sensor = shared memory word
        self.assertRegex(self.text, r"pv_hmi\s+AT %QW1")
        self.assertRegex(self.text, r"trip_cmd\s+AT %QX0.0")
        self.assertRegex(self.text, r"trip_status AT %QX0.1")
        self.assertRegex(self.text, r"override\s+AT %QX0.2")
        self.assertEqual(int(R.SisHolding.PV), R.MW_HOLDING_BASE)
        self.assertEqual(int(R.SisHolding.PV_HMI), 1)
        self.assertEqual(int(R.SisCoil.TRIP_CMD), 0)
        self.assertEqual(int(R.SisCoil.TRIP_STATUS), 1)
        self.assertEqual(int(R.SisCoil.OVERRIDE), 2)

    def test_template_default_is_tank(self):
        # The committed default compiles standalone and equals the tank trip.
        m = TRIP_RE.search(self.text)
        self.assertIsNotNone(m, "template must keep a 'TRIP_HH : INT := <n>;' line")
        self.assertEqual(int(m.group(1)), scenarios.get("tank").trip_reg)

    def test_override_suppresses_and_freezes(self):
        # The attack path must exist in the logic (force/bypass branch).
        self.assertIn("IF override THEN", self.text)
        self.assertIn("trip_cmd    := FALSE;", self.text)


class TestScenarioSubstitution(unittest.TestCase):
    def test_each_scenario_gets_its_trip_reg(self):
        for sid in scenarios.ids():
            prog = sis_program_for(sid)
            m = TRIP_RE.search(prog)
            self.assertIsNotNone(m, sid)
            self.assertEqual(int(m.group(1)), scenarios.get(sid).trip_reg, sid)

    def test_substitution_changes_only_the_constant(self):
        tank = sis_program_for("tank")
        reactor = sis_program_for("reactor")
        # Differ only on the TRIP_HH numeric line.
        diff = [(a, b) for a, b in zip(tank.splitlines(), reactor.splitlines())
                if a != b]
        self.assertEqual(len(diff), 1)
        self.assertIn("TRIP_HH", diff[0][0])


if __name__ == "__main__":
    unittest.main()
