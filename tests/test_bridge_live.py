"""Live Modbus round-trip against the real OpenPLC instances (opt-in).

Skipped unless PETROTWIN_LIVE=1 and pymodbus is installed and both OpenPLC
slaves are reachable with their programs loaded and running. This is the
TESTING.md section-3 bridge round-trip against the real stack; the default CI
path is the dependency-free fake-PLC tests in test_bridge.py.

Prerequisites (see plc/README.md):
  cd plc && docker compose up -d
  python3 -m plc.load_scenario tank   # loads+compiles+starts BPCS & SIS (match SCENARIO)
  PETROTWIN_LIVE=1 python3 -m unittest tests.test_bridge_live -v

Note: the SIS high-level trip latches and has no reset input in Phase 0, so
tests that trip it mutate PLC state. Restart the SIS runtime between runs for a
clean latch. The override tests are robust regardless of the latch.
"""

import os
import time
import unittest

from common import registers as R
from common import scenarios

LIVE = os.environ.get("PETROTWIN_LIVE") == "1"
# The live SIS must have the matching scenario program loaded (default tank).
SC = scenarios.get(os.environ.get("PETROTWIN_SCENARIO", scenarios.DEFAULT_SCENARIO))


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
class TestLiveRoundTrip(unittest.TestCase):
    def setUp(self):
        from bridge.plc_io import ModbusPlc
        self.bpcs = ModbusPlc.for_instance(R.Instance.BPCS)
        self.sis = ModbusPlc.for_instance(R.Instance.SIS)

    def tearDown(self):
        self.bpcs.close()
        self.sis.close()

    @staticmethod
    def _wait_coil(plc, addr, expected, timeout=1.5):
        """Poll a coil until it matches expected (allows for PLC scan latency)."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = plc.read_coil(addr)
            if last == expected:
                return True
            time.sleep(0.05)
        return last == expected

    def test_sensor_holding_round_trip(self):
        # Write a PV into each PLC's sensor register, read it straight back.
        for value in (0, 2000, 5000):
            self.bpcs.write_holding(R.BpcsHolding.PV, value)
            self.sis.write_holding(R.SisHolding.PV, value)
            self.assertEqual(self.bpcs.read_holding(R.BpcsHolding.PV), value)
            self.assertEqual(self.sis.read_holding(R.SisHolding.PV), value)

    def test_bpcs_control_loop_tracks_setpoint(self):
        # With bpcs.st running, the actuator command must follow the integer
        # proportional law for (PV, SETPOINT) — i.e. the program REACTS, not just
        # a read-back. Includes the off-scale attack setpoint (actuator saturates).
        def idiv(a, b):
            return int(a / b)  # trunc toward zero, matching IEC/ST

        def expected(pv, sp):
            raw = SC.ctrl_bias_pm + idiv(SC.ctrl_kp_num * (sp - pv), SC.ctrl_kp_den)
            return max(0, min(R.VALVE_REG_MAX, raw))

        cases = [
            (SC.setpoint_reg, SC.setpoint_reg),                 # at setpoint -> bias
            (SC.setpoint_reg, SC.attack_setpoint_reg),          # attack -> saturated
            (min(SC.reg_full_scale, SC.setpoint_reg + 300), SC.setpoint_reg),
        ]
        for pv, sp in cases:
            self.bpcs.write_holding(R.BpcsHolding.PV, pv)
            self.bpcs.write_holding(R.BpcsHolding.SETPOINT, sp)
            exp, deadline, val = expected(pv, sp), time.time() + 1.5, None
            while time.time() < deadline:
                val = self.bpcs.read_holding(R.BpcsHolding.FEED_CMD)
                if val == exp:
                    break
                time.sleep(0.05)
            self.assertEqual(val, exp, f"pv={pv} sp={sp}")

    def test_trip_then_override_suppresses_it(self):
        # Healthy SIS: at/above the threshold the trip asserts.
        above_trip = min(SC.reg_full_scale, SC.trip_reg + 500)
        self.sis.write_coil(R.SisCoil.OVERRIDE, False)
        self.sis.write_holding(R.SisHolding.PV, above_trip)
        self.assertTrue(self._wait_coil(self.sis, R.SisCoil.TRIP_CMD, True),
                        "trip did not assert at/above threshold")
        # Attack: override suppresses the trip command regardless of level.
        self.sis.write_coil(R.SisCoil.OVERRIDE, True)
        self.assertTrue(self._wait_coil(self.sis, R.SisCoil.TRIP_CMD, False),
                        "override did not suppress the trip")
        self.assertTrue(self._wait_coil(self.sis, R.SisCoil.TRIP_STATUS, False),
                        "override did not force status to armed-normal")


if __name__ == "__main__":
    unittest.main()
