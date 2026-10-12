"""Tests for the scenario registry (common/scenarios.py).

Guards the single source of per-scenario truth: integer Modbus scaling
round-trips and clamps, the register ranges fit a 16-bit Modbus register, and
the cross-scenario invariants the rest of the testbed relies on (failure ==
full scale, trip below failure, PV starts below the trip).
"""

import unittest

from common import scenarios

REG_CEILING = 65535


class TestRegistry(unittest.TestCase):
    def test_ids_present_and_ordered(self):
        self.assertEqual(scenarios.ids(), ["tank", "vessel", "reactor"])
        for sid in scenarios.ids():
            self.assertIn(sid, scenarios.SCENARIOS)

    def test_get_unknown_raises(self):
        with self.assertRaises(KeyError):
            scenarios.get("nope")

    def test_default_is_tank(self):
        self.assertEqual(scenarios.DEFAULT_SCENARIO, "tank")


class TestScalingAndRanges(unittest.TestCase):
    def test_reg_round_trip(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            for frac in (0.0, 0.1, 0.5, 0.9, 1.0):
                pv = frac * sc.pv_full_scale
                back = sc.reg_to_pv(sc.pv_to_reg(pv))
                self.assertAlmostEqual(back, pv, delta=1.0 / sc.reg_scale)

    def test_reg_clamps_to_range(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.pv_to_reg(-10.0), 0)
            self.assertEqual(sc.pv_to_reg(1e9), sc.reg_full_scale)

    def test_ranges_fit_u16(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertLessEqual(sc.reg_full_scale, REG_CEILING)
            self.assertLessEqual(sc.trip_reg, REG_CEILING)
            self.assertGreater(sc.reg_full_scale, 0)

    def test_fraction_clamped_0_1(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.fraction(-5.0), 0.0)
            self.assertEqual(sc.fraction(sc.fail_at * 2), 1.0)
            self.assertAlmostEqual(sc.fraction(sc.fail_at), 1.0)


class TestInvariants(unittest.TestCase):
    def test_full_scale_is_failure(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.pv_full_scale, sc.fail_at)

    def test_trip_strictly_between_start_and_failure(self):
        # The PV starts below the trip, and the trip is below failure, so a
        # healthy SIS has room to act before the process fails.
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertLess(sc.pv0, sc.trip_hh, sid)
            self.assertLess(sc.trip_hh, sc.fail_at, sid)

    def test_trip_reg_matches_scaling(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.trip_reg, round(sc.trip_hh * sc.reg_scale))


class TestControlLaw(unittest.TestCase):
    def test_setpoint_reg_matches_scaling(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.setpoint_reg, round(sc.setpoint * sc.reg_scale))

    def test_attack_setpoint_is_off_scale_but_fits_register(self):
        # The malicious setpoint sits ABOVE full scale so the actuator stays
        # saturated past failure, and still fits a signed 16-bit register.
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertGreater(sc.attack_setpoint_reg, sc.reg_full_scale, sid)
            self.assertLessEqual(sc.attack_setpoint_reg, 32767, sid)

    def test_control_gain_well_formed_and_signed(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertGreater(sc.ctrl_kp_den, 0, sid)
            self.assertNotEqual(sc.ctrl_kp_num, 0, sid)
            self.assertTrue(0 <= sc.ctrl_bias_pm <= 1000, sid)
        # The reactor's coolant valve removes energy -> negative gain; the others
        # add to the PV -> positive gain.
        self.assertLess(scenarios.get("reactor").ctrl_kp_num, 0)
        self.assertGreater(scenarios.get("tank").ctrl_kp_num, 0)
        self.assertGreater(scenarios.get("vessel").ctrl_kp_num, 0)

    def test_bias_holds_setpoint_at_zero_error(self):
        # At PV == setpoint the control term is zero, so feed_cmd == bias: that
        # is the nominal baseline actuator position (not saturated).
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertLess(sc.ctrl_bias_pm, 1000, sid)
            self.assertGreater(sc.ctrl_bias_pm, 0, sid)


class TestSifRealism(unittest.TestCase):
    """The real-SIF realism fields: setpoint clamp, pre-alarm, reset deadband."""

    def test_setpoint_clamp_band_ordering(self):
        # Operator band is a strict interval containing the baseline setpoint.
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertLess(sc.sp_lo, sc.sp_hi, sid)
            self.assertLessEqual(sc.sp_lo, sc.setpoint, sid)
            self.assertLessEqual(sc.setpoint, sc.sp_hi, sid)

    def test_clamp_high_below_or_at_trip(self):
        # tank/vessel: the clamped-high setpoint is strictly below the trip, so a
        # setpoint-tamper settles safe. reactor is the documented exception
        # (sp_hi == trip_hh: its exotherm trips even at the clamp).
        self.assertLess(scenarios.get("tank").sp_hi, scenarios.get("tank").trip_hh)
        self.assertLess(scenarios.get("vessel").sp_hi, scenarios.get("vessel").trip_hh)
        self.assertLessEqual(
            scenarios.get("reactor").sp_hi, scenarios.get("reactor").trip_hh
        )

    def test_pre_alarm_between_setpoint_and_trip(self):
        # "H" sits above the operating band and strictly below the "HH" trip.
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertGreaterEqual(sc.pre_alarm_h, sc.sp_hi, sid)
            self.assertLess(sc.pre_alarm_h, sc.trip_hh, sid)

    def test_reset_deadband_positive_and_below_trip(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertGreater(sc.reset_deadband, 0.0, sid)
            self.assertGreater(sc.reset_db_reg, 0, sid)
            # The reset permissive (trip - deadband) stays above the setpoint, so
            # a reset is only possible once the process is genuinely back down.
            self.assertGreater(sc.trip_hh - sc.reset_deadband, sc.setpoint, sid)

    def test_derived_regs_match_scaling(self):
        for sid in scenarios.ids():
            sc = scenarios.get(sid)
            self.assertEqual(sc.sp_lo_reg, round(sc.sp_lo * sc.reg_scale), sid)
            self.assertEqual(sc.sp_hi_reg, round(sc.sp_hi * sc.reg_scale), sid)
            self.assertEqual(
                sc.pre_alarm_h_reg, round(sc.pre_alarm_h * sc.reg_scale), sid
            )
            self.assertEqual(
                sc.reset_db_reg, round(sc.reset_deadband * sc.reg_scale), sid
            )

    def test_attack_manual_out_maps_to_gain_sign(self):
        # Positive gain (open raises PV) -> drive wide open; negative gain
        # (coolant valve) -> drive shut.
        self.assertEqual(scenarios.get("tank").attack_manual_out_reg, 1000)
        self.assertEqual(scenarios.get("vessel").attack_manual_out_reg, 1000)
        self.assertEqual(scenarios.get("reactor").attack_manual_out_reg, 0)

    def test_rate_limit_constant_sane(self):
        self.assertGreater(scenarios.CTRL_RATE_LIM_PM, 0)
        self.assertLessEqual(scenarios.CTRL_RATE_LIM_PM, 1000)


class TestProductStorage(unittest.TestCase):
    """P-401 terminal sink: alarm ordering and the hold-when-exporting choice."""

    def setUp(self):
        self.p = scenarios.PRODUCT

    def test_alarm_levels_ordered(self):
        p = self.p
        self.assertLess(p.ll, p.lo)
        self.assertLess(p.lo, p.hi)
        self.assertLess(p.hi, p.hh)
        self.assertLess(p.hh, p.height_full)
        self.assertLess(0.0, p.ll)

    def test_export_open_holds_level(self):
        # q_export == q_fill is the no-baseline-drift choice: with EXPORT open
        # the net flow is zero, so the level neither fills nor drains.
        self.assertEqual(self.p.q_export, self.p.q_fill)

    def test_initial_level_in_normal_band(self):
        p = self.p
        self.assertLess(p.lo, p.level0)
        self.assertLess(p.level0, p.hi)

    def test_reg_scaling(self):
        p = self.p
        self.assertEqual(p.reg_full_scale, round(p.height_full * p.reg_scale))
        self.assertEqual(p.pv_to_reg(-1.0), 0)
        self.assertEqual(p.pv_to_reg(1e6), p.reg_full_scale)
        self.assertAlmostEqual(p.fraction(p.height_full), 1.0)
        self.assertEqual(p.fraction(-1.0), 0.0)


if __name__ == "__main__":
    unittest.main()
