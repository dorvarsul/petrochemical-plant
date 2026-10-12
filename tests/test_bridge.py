"""Integrated-loop tests for the Modbus bridge, using the fake PLCs.

These exercise the whole scenario logic for ALL THREE scenarios, without Docker
or OpenPLC (TESTING.md sections 3 and 5):

  * baseline — the BPCS holds the PV at its safe setpoint; the SIS never trips
    and the process never fails, no matter how long it runs;
  * setpoint attack alone — a malicious setpoint drives the process up, but a
    healthy SIS trips and holds it safe (defense in depth works);
  * full attack — malicious setpoint AND SIS_OVERRIDE: the process runs to
    failure while the HMI stays frozen and "armed";
  * override alone — with the setpoint untouched, defeating the SIS changes
    nothing (the setpoint is the attack's driver).

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
    b = Bridge(make_model(sid), FakeBpcs(sc), FakeSis(sc.trip_reg), dt=DT)
    b.prime_setpoint()
    return b


def attack(b):
    """The external attack: malicious setpoint + SIS override, over Modbus."""
    b.bpcs.write_holding(R.BpcsHolding.SETPOINT, b.scenario.attack_setpoint_reg)
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


class TestSetpointAttackCaughtBySis(unittest.TestCase):
    """Setpoint manipulation alone: a healthy SIS trips and holds it safe."""

    def test_sis_trips_and_process_never_fails(self):
        for sid in ALL:
            with self.subTest(sid=sid):
                b = make_bridge(sid)
                # Attacker moves the setpoint but does NOT defeat the SIS.
                b.bpcs.write_holding(R.BpcsHolding.SETPOINT,
                                     b.scenario.attack_setpoint_reg)
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
