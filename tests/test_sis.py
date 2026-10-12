"""Unit tests for the real-SIF behaviour of FakeSis (lock-step with sis.st).

Exercises the Safety PLC logic directly: the latched high-high trip, the
maintenance bypass (freeze + suppress), the pre-alarm "H", the bad-PV fail-safe,
the deadband-gated manual reset, and the block-valve final element. The matching
ST program (plc/sis/sis.st) must stay byte-for-byte logically identical.
"""

import unittest

from bridge.plc_io import FakeSis
from common import registers as R
from common import scenarios


def drive(sis, pv_reg, *, override=False, reset=False, blk_open=True):
    """Set the inputs, run one scan, return the SIS."""
    sis.write_holding(R.SisHolding.PV, pv_reg)
    sis.write_coil(R.SisCoil.OVERRIDE, override)
    sis.write_coil(R.SisCoil.RESET, reset)
    sis.write_coil(R.SisCoil.BLK_OPEN_CMD, blk_open)
    sis.scan()
    return sis


class TestTripAndPreAlarm(unittest.TestCase):
    def setUp(self):
        self.sc = scenarios.get("tank")  # trip 9000, H 8500, db 500, full 10000

    def test_pre_alarm_edges_below_trip(self):
        sis = FakeSis(self.sc)
        drive(sis, 8400)
        self.assertFalse(sis.read_coil(R.SisCoil.PREALARM_H))
        self.assertFalse(sis.read_coil(R.SisCoil.TRIP_CMD))
        drive(sis, 8500)
        self.assertTrue(sis.read_coil(R.SisCoil.PREALARM_H))
        self.assertFalse(sis.read_coil(R.SisCoil.TRIP_CMD))

    def test_trip_latches_at_threshold_and_holds(self):
        sis = FakeSis(self.sc)
        drive(sis, 8999)
        self.assertFalse(sis.read_coil(R.SisCoil.TRIP_CMD))
        drive(sis, 9000)
        self.assertTrue(sis.read_coil(R.SisCoil.TRIP_CMD))
        drive(sis, 4000)  # PV falls, but the trip is latched
        self.assertTrue(sis.read_coil(R.SisCoil.TRIP_CMD))


class TestManualReset(unittest.TestCase):
    def setUp(self):
        self.sc = scenarios.get("tank")  # reset permits only when PV < 8500

    def test_reset_blocked_until_below_deadband(self):
        sis = FakeSis(self.sc)
        drive(sis, 9500)                       # trip
        self.assertTrue(sis.read_coil(R.SisCoil.TRIP_CMD))
        drive(sis, 8600, reset=True)           # still within the deadband
        self.assertTrue(sis.read_coil(R.SisCoil.TRIP_CMD),
                        "reset cleared the latch while PV >= trip - deadband")
        drive(sis, 8400, reset=True)           # below trip - deadband
        self.assertFalse(sis.read_coil(R.SisCoil.TRIP_CMD))

    def test_reset_needs_the_pushbutton(self):
        sis = FakeSis(self.sc)
        drive(sis, 9500)
        drive(sis, 4000, reset=False)          # back to safe, but no reset
        self.assertTrue(sis.read_coil(R.SisCoil.TRIP_CMD))
        drive(sis, 4000, reset=True)
        self.assertFalse(sis.read_coil(R.SisCoil.TRIP_CMD))


class TestBadPv(unittest.TestCase):
    def setUp(self):
        self.sc = scenarios.get("tank")  # valid PV range [0, 10000]

    def test_out_of_range_forces_trip_and_blocks_reset(self):
        sis = FakeSis(self.sc)
        drive(sis, 11000)                      # above full scale
        self.assertTrue(sis.read_coil(R.SisCoil.PV_BAD))
        self.assertTrue(sis.read_coil(R.SisCoil.TRIP_CMD))
        self.assertFalse(sis.read_coil(R.SisCoil.PREALARM_H),
                         "pre-alarm must be gated off on a bad PV")
        # A reset is refused while the PV is still bad, even at a low reading.
        sis.write_holding(R.SisHolding.PV, 20000)
        sis.write_coil(R.SisCoil.RESET, True)
        sis.scan()
        self.assertTrue(sis.read_coil(R.SisCoil.TRIP_CMD))

    def test_negative_pv_is_bad(self):
        sis = FakeSis(self.sc)
        drive(sis, -1)
        self.assertTrue(sis.read_coil(R.SisCoil.PV_BAD))
        self.assertTrue(sis.read_coil(R.SisCoil.TRIP_CMD))


class TestBypassAndBlockValve(unittest.TestCase):
    def setUp(self):
        self.sc = scenarios.get("tank")

    def test_bypass_suppresses_trip_and_freezes_hmi(self):
        sis = FakeSis(self.sc)
        drive(sis, 5000)                       # nominal, HMI tracks
        self.assertEqual(sis.read_holding(R.SisHolding.PV_HMI), 5000)
        drive(sis, 9500, override=True)        # bypass while over the trip
        self.assertTrue(sis.read_coil(R.SisCoil.BYPASS_ACTIVE))
        self.assertFalse(sis.read_coil(R.SisCoil.TRIP_CMD))
        self.assertFalse(sis.read_coil(R.SisCoil.TRIP_STATUS))
        self.assertEqual(sis.read_holding(R.SisHolding.PV_HMI), 5000,
                         "HMI PV must freeze under bypass")

    def test_block_valve_follows_cmd_and_trip(self):
        sis = FakeSis(self.sc)
        drive(sis, 5000, blk_open=True)
        self.assertTrue(sis.read_coil(R.SisCoil.BLK_OUT))
        drive(sis, 5000, blk_open=False)
        self.assertFalse(sis.read_coil(R.SisCoil.BLK_OUT))
        drive(sis, 9500, blk_open=True)        # trip overrides the open request
        self.assertTrue(sis.read_coil(R.SisCoil.TRIP_CMD))
        self.assertFalse(sis.read_coil(R.SisCoil.BLK_OUT),
                         "a trip must shut the block valve")


if __name__ == "__main__":
    unittest.main()
