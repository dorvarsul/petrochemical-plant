"""Integrated-loop tests for the Modbus bridge, using the fake PLCs.

These exercise the whole scenario logic for ALL THREE scenarios, without Docker
or OpenPLC (TESTING.md sections 3 and 5):

  * baseline — the BPCS holds the PV at its safe setpoint; the SIS never trips
    and the process never fails, no matter how long it runs;
  * setpoint tamper alone — an off-scale setpoint is CLAMPED by the BPCS, so the
    naive attack is neutralised: the process never fails (tank/vessel settle at
    the clamp below the trip; the reactor's exotherm still trips its healthy SIS);
  * manual drive, SIS armed — the attacker seizes the actuator through BPCS
    MANUAL mode, but a healthy SIS trips and holds it safe (defense in depth);
  * full attack — manual drive AND SIS_OVERRIDE: the process runs to failure
    while the HMI stays frozen and "armed";
  * override alone — with the actuator untouched, defeating the SIS changes
    nothing (the actuator drive is the attack's driver).

The live round-trip against real OpenPLC is tests/test_bridge_live.py.
"""

import unittest

from bridge.bridge import Bridge
from bridge.plc_io import FakeBpcs, FakeSis
from common import registers as R
from common import scenarios
from physics import make_model

DT = 0.1
ALL = scenarios.ids()


def make_bridge(sid):
    """A primed bridge: PLCs wired, safe operator setpoint seeded."""
    sc = scenarios.get(sid)
    b = Bridge(make_model(sid), FakeBpcs(sc), FakeSis(sc), dt=DT)
    b.prime_setpoint()
    return b


def manual_drive(b):
    """Seize the actuator: flip the BPCS to MANUAL and drive the output to the
    dangerous extreme (the evolved attack's driver), SIS left untouched."""
    b.bpcs.write_coil(R.BpcsCoil.MAN_MODE, True)
    b.bpcs.write_holding(R.BpcsHolding.MAN_OUT, b.scenario.attack_manual_out_reg)


def attack(b):
    """The full external attack: manual actuator drive + SIS override."""
    manual_drive(b)
    b.sis.write_coil(R.SisCoil.OVERRIDE, True)


class TestWiring(unittest.TestCase):
    def test_sensor_injected_into_both_plcs(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                b.step()
                reg = b.scenario.pv_to_reg(b.scenario.pv0)  # published at step start
                self.assertEqual(b.bpcs.read_holding(R.BpcsHolding.PV), reg)
                self.assertEqual(b.sis.read_holding(R.SisHolding.PV), reg)

    def test_setpoint_seeded_and_reported(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                s = b.step()
                self.assertEqual(b.bpcs.read_holding(R.BpcsHolding.SETPOINT),
                                 b.scenario.setpoint_reg)
                self.assertAlmostEqual(s.setpoint, b.scenario.setpoint,
                                       delta=1.0 / b.scenario.reg_scale)
                self.assertFalse(s.override)


class TestBaselineHoldsSetpoint(unittest.TestCase):
    """With no attacker the BPCS holds setpoint; the SIS never needs to trip."""

    def test_holds_setpoint_never_trips_never_fails(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                sc = b.scenario
                max_pv = 0.0
                for _ in range(6000):  # 600 s of process time
                    s = b.step()
                    self.assertFalse(s.trip_cmd, f"{sid}: SIS tripped at baseline")
                    self.assertFalse(b.model.failed, f"{sid}: baseline failed")
                    max_pv = max(max_pv, s.true_pv)
                # Stayed near the operator setpoint, comfortably below the trip.
                self.assertLess(max_pv, sc.trip_hh, f"{sid}: drifted to the trip")
                self.assertAlmostEqual(b.model.pv, sc.setpoint,
                                       delta=0.05 * sc.fail_at,
                                       msg=f"{sid}: did not hold setpoint")

    def test_hmi_tracks_truth_at_baseline(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                for _ in range(200):
                    s = b.step()
                    self.assertLess(s.divergence, 0.02 * b.scenario.fail_at)


class TestSetpointClampNeutralizesNaiveAttack(unittest.TestCase):
    """An off-scale setpoint is clamped by the BPCS, so the naive attack never
    fails the process: tank/vessel settle at the clamp below the trip; the
    reactor's exotherm is caught by its healthy SIS instead."""

    def test_clamp_holds_or_sis_catches_but_never_fails(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                sc = b.scenario
                # Attacker tampers ONLY the setpoint (no manual drive, no override).
                b.bpcs.write_holding(R.BpcsHolding.SETPOINT, sc.attack_setpoint_reg)
                tripped_seen = False
                max_pv = 0.0
                s = None
                for _ in range(8000):
                    s = b.step()
                    tripped_seen = tripped_seen or s.trip_cmd
                    max_pv = max(max_pv, s.true_pv)
                    if b.model.failed:
                        break
                # The invariant across all three: a setpoint tamper never fails.
                self.assertFalse(b.model.failed,
                                 f"{sid}: setpoint tamper reached failure")
                # The BPCS annunciated that it clamped the out-of-range setpoint.
                self.assertTrue(b.bpcs.read_coil(R.BpcsCoil.SP_CLAMPED),
                                f"{sid}: SP_CLAMPED not annunciated")
                # The HMI is truthful (no override): it tracks the true PV.
                self.assertLess(s.divergence, 0.05 * sc.fail_at,
                                f"{sid}: HMI diverged without an override")
                if sid in ("tank", "vessel"):
                    # The clamp alone holds these below the trip — no trip at all.
                    self.assertFalse(tripped_seen,
                                     f"{sid}: clamp should hold below the trip")
                    self.assertLessEqual(max_pv, sc.sp_hi + 0.05 * sc.fail_at,
                                         f"{sid}: settled above the clamp")
                else:
                    # The reactor still reaches its trip; the healthy SIS catches it.
                    self.assertTrue(tripped_seen,
                                    f"{sid}: reactor SIS should have tripped")


class TestManualDriveCaughtBySis(unittest.TestCase):
    """The evolved driver (BPCS MANUAL) with the SIS left armed: defense in
    depth — a healthy SIS trips and holds the process safe."""

    def test_sis_trips_and_process_never_fails(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                manual_drive(b)  # SIS NOT defeated
                tripped_seen = False
                max_pv = 0.0
                for _ in range(8000):
                    s = b.step()
                    tripped_seen = tripped_seen or s.trip_cmd
                    max_pv = max(max_pv, s.true_pv)
                    if b.model.failed:
                        break
                self.assertTrue(tripped_seen, f"{sid}: SIS never tripped")
                self.assertFalse(b.model.failed,
                                 f"{sid}: failed despite a healthy SIS")
                self.assertLess(max_pv, b.scenario.fail_at)
                self.assertTrue(b.hmi_trip_status(),
                                f"{sid}: HMI should show tripped after a real trip")


class TestFullAttack(unittest.TestCase):
    def test_failure_while_hmi_frozen_and_armed(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                sc = b.scenario

                # Baseline for a while; capture what the operator saw.
                for _ in range(40):
                    normal = b.step()
                self.assertFalse(normal.trip_cmd)
                frozen_hmi = normal.hmi_pv
                self.assertAlmostEqual(frozen_hmi, b.model.pv,
                                       delta=0.02 * sc.fail_at)

                attack(b)

                failed = False
                for _ in range(8000):
                    s = b.step()
                    self.assertFalse(s.trip_cmd, f"{sid}: trip fired despite override")
                    self.assertFalse(s.hmi_trip_status,
                                     f"{sid}: HMI left 'armed' under attack")
                    self.assertAlmostEqual(s.hmi_pv, frozen_hmi, delta=1e-6,
                                           msg=f"{sid}: HMI PV was not frozen")
                    self.assertTrue(s.override)
                    if b.model.failed:
                        failed = True
                        break
                self.assertTrue(failed, f"{sid}: attack did not drive to failure")
                # Acceptance F: a large, unmistakable gap between truth and HMI.
                self.assertGreater(s.true_pv - s.hmi_pv, 0.4 * sc.fail_at)

    def test_override_alone_is_insufficient(self):
        # Defeating the SIS without moving the setpoint changes nothing: the
        # BPCS keeps holding the safe setpoint, so the process never fails.
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                b.sis.write_coil(R.SisCoil.OVERRIDE, True)  # SIS defeated, setpoint safe
                for _ in range(6000):
                    s = b.step()
                    self.assertFalse(s.trip_cmd)
                    self.assertFalse(b.model.failed,
                                     f"{sid}: failed from override alone")
                self.assertAlmostEqual(b.model.pv, b.scenario.setpoint,
                                       delta=0.05 * b.scenario.fail_at)


if __name__ == "__main__":
    unittest.main()
