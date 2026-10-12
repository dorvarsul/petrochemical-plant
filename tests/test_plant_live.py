"""Live connected-plant round-trip against the REAL OpenPLC pair (opt-in).

Skipped unless PETROTWIN_LIVE=1, pymodbus is installed, and both OpenPLC slaves
are reachable. This is the connected-plant analogue of test_bridge_live.py: all
three units run on one BPCS slave + one SIS slave (each unit in its own offset
block), driven through ``make_live_plant_bridge``. The default CI path is the
dependency-free fake-PLC plant tests in test_plant.py.

Prerequisites:
  cd plc && docker compose up -d
  PETROTWIN_LIVE=1 python3 -m unittest tests.test_plant_live -v
(the test loads the multi-unit programs itself via plc.load_scenario.load_plant)
"""

import os
import time
import unittest

from common import registers as R

LIVE = os.environ.get("PETROTWIN_LIVE") == "1"


def _plc_available():
    try:
        import pymodbus  # noqa: F401
    except ImportError:
        return False
    import socket
    for _inst, (host, port) in R.ENDPOINTS.items():
        s = socket.socket(); s.settimeout(1)
        try:
            s.connect((host, port))
        except OSError:
            return False
        finally:
            s.close()
    return True


@unittest.skipUnless(LIVE, "set PETROTWIN_LIVE=1 to run live OpenPLC tests")
@unittest.skipUnless(_plc_available(), "pymodbus missing or OpenPLC slaves unreachable")
class TestLivePlant(unittest.TestCase):
    def setUp(self):
        from plc.load_scenario import load_plant
        from bridge.plant_bridge import make_live_plant_bridge
        load_plant()  # generate + load the two multi-unit programs, compile, start
        self.bridge = make_live_plant_bridge(dt=1.0)
        for _ in range(12):   # warm up: let the real 100 ms PLC tasks settle
            self.s = self.bridge.step(); time.sleep(0.12)

    def tearDown(self):
        self.bridge.close()

    def test_baseline_holds_all_units_at_setpoint(self):
        for uid, u in self.s.units.items():
            sc = self.bridge.plant.unit(uid).scenario
            self.assertFalse(u.trip_cmd, f"{uid} tripped at baseline")
            self.assertFalse(u.failed, f"{uid} failed at baseline")
            # HMI tracks truth (nothing spoofed yet)
            self.assertLess(abs(u.hmi_pv - u.true_pv), 0.05 * sc.pv_full_scale)

    def test_attack_one_unit_spoofs_while_others_hold(self):
        tgt = "reactor"
        frozen = self.bridge.hmi_pv(tgt)
        self.bridge.attack(tgt, bypass=True)   # BPCS -> MANUAL extreme + SIS bypass
        for _ in range(40):
            s = self.bridge.step(); time.sleep(0.04)
        u = s.units[tgt]
        # the true PV diverges from the frozen/armed HMI (the spoof)
        self.assertTrue(u.override)
        self.assertFalse(u.hmi_trip_status, "HMI shows armed while bypassed")
        self.assertGreater(u.true_pv - u.hmi_pv, 1.0, "no spoof gap opened")
        self.assertAlmostEqual(u.hmi_pv, frozen, delta=0.6)
        # the other two units are undisturbed
        for other in ("tank", "vessel"):
            ou = s.units[other]
            sc = self.bridge.plant.unit(other).scenario
            self.assertFalse(ou.failed, f"{other} failed (should be undisturbed)")
            self.assertLess(abs(ou.true_pv - sc.setpoint), 0.15 * sc.pv_full_scale)


if __name__ == "__main__":
    unittest.main()
