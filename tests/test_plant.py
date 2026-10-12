"""Connected-plant acceptance tests (Phase 1).

Exercises the whole flowsheet — T-101 feed tank -> R-301 reactor -> V-201
vessel — running at once on fake PLCs (one BPCS + one SIS per unit), through the
``PlantBridge``. These are the Phase-1 "done" criteria from ROADMAP.md:

  * with no attacker the whole plant settles to a connected steady state and no
    SIS trips;
  * attacking ONE unit drives THAT unit past its failure threshold while its HMI
    stays frozen/armed, and the up/downstream units respond plausibly (forward
    coupling) without failing;
  * the healthy-SIS contrast (manual drive, SIS armed) holds the targeted unit safe;
  * the coupling is forward only (tank->reactor->vessel): an attack on a
    downstream unit leaves its upstream neighbours undisturbed, and an upstream
    attack visibly moves its downstream neighbour.

Dependency-free (stdlib ``unittest`` + the in-memory fake PLCs), same as
``tests/test_bridge.py``. See TESTING.md.
"""

from __future__ import annotations

import unittest

from bridge.plant_bridge import make_fake_plant_bridge
from physics import plant as P
from physics.plant import PlantModel
from physics.reactor import ReactorModel
from physics.vessel import VesselModel

UNITS = ("tank", "reactor", "vessel")
DT = 0.1
ATTACK_AT = 10.0


def run_plant(attack_unit=None, bypass=True, t_end=400.0,
              t_attack=ATTACK_AT, dt=DT):
    """Run the connected plant; optionally attack one unit at ``t_attack``.

    ``bypass`` selects the full attack (manual drive + SIS_OVERRIDE) vs. the
    defence-in-depth contrast (manual drive, SIS left armed). Returns
    ``(bridge, last_sample, frozen_hmi)`` where ``frozen_hmi`` is the attacked
    unit's HMI PV captured at the moment of attack (None if no attack).
    """
    b = make_fake_plant_bridge(dt=dt)
    attacked = False
    frozen = None
    s = None
    for _ in range(int(t_end / dt)):
        if attack_unit and not attacked and b.plant.time >= t_attack:
            frozen = b.hmi_pv(attack_unit)
            b.attack(attack_unit, bypass=bypass)
            attacked = True
        s = b.step()
    return b, s, frozen


class TestCouplingNeutrality(unittest.TestCase):
    """The coupling hooks are neutral by default — standalone physics unchanged."""

    def test_default_couplings_are_zero(self):
        self.assertEqual(ReactorModel().q_coupling, 0.0)
        self.assertEqual(VesselModel().p_coupling, 0.0)

    def test_reset_clears_couplings(self):
        rx = ReactorModel()
        rx.q_coupling = 1.0
        rx.reset()
        self.assertEqual(rx.q_coupling, 0.0)
        v = VesselModel()
        v.p_coupling = 1.0
        v.reset()
        self.assertEqual(v.p_coupling, 0.0)


class TestPlantModel(unittest.TestCase):
    """The composed model's structure and couplings."""

    def test_units_in_flow_order(self):
        plant = PlantModel()
        self.assertEqual([uid for uid, _ in plant.units], list(P.FLOW))
        self.assertEqual(P.FLOW, UNITS)

    def test_couplings_zero_at_setpoint(self):
        # Fresh plant starts every unit at (or below) its setpoint, so one step
        # with each unit held at its baseline command leaves couplings at zero.
        plant = PlantModel()
        cmds = {"tank": (0.4, False), "reactor": (0.5, False),
                "vessel": (0.2, False)}
        plant.step(DT, cmds)
        self.assertAlmostEqual(plant.q_coupling, 0.0, places=9)
        self.assertAlmostEqual(plant.p_coupling, 0.0, places=6)

    def test_unknown_unit_raises(self):
        with self.assertRaises(KeyError):
            PlantModel().unit("boiler")

    def test_snapshot_has_every_unit(self):
        snap = PlantModel().snapshot()
        self.assertEqual(set(snap["units"]), set(UNITS))
        for uid in UNITS:
            u = snap["units"][uid]
            self.assertIn("pv", u)
            self.assertIn("fraction", u)
            self.assertIn("failed", u)


class TestConnectedSteadyState(unittest.TestCase):
    """No attacker -> the whole plant holds, no SIS trips, no failures."""

    def test_baseline_holds(self):
        b, s, _ = run_plant(attack_unit=None, t_end=120.0)
        self.assertFalse(s.any_failed)
        tol = {"tank": 0.05, "reactor": 1.0, "vessel": 0.1}
        for uid, u in s.units.items():
            with self.subTest(unit=uid):
                sp = b.plant.unit(uid).scenario.setpoint
                self.assertFalse(u.trip_cmd, f"{uid} tripped at baseline")
                self.assertLess(abs(u.true_pv - sp), tol[uid],
                                f"{uid} drifted off setpoint")


class TestPerUnitAttack(unittest.TestCase):
    """Full attack on one unit -> that unit fails, HMI spoofed, others survive."""

    def test_target_fails_while_hmi_frozen(self):
        for unit in UNITS:
            with self.subTest(unit=unit):
                b, s, frozen = run_plant(attack_unit=unit, t_end=400.0)
                tgt = s.units[unit]
                sc = b.plant.unit(unit).scenario
                # Target driven past its failure threshold ...
                self.assertTrue(tgt.failed, f"{unit} did not fail")
                # ... while its operator HMI stays armed and frozen ...
                self.assertFalse(tgt.hmi_trip_status,
                                 f"{unit} HMI shows tripped")
                self.assertAlmostEqual(tgt.hmi_pv, frozen, delta=1.0 / sc.reg_scale,
                                       msg=f"{unit} HMI not frozen")
                # ... producing a large, unmistakable spoof gap.
                self.assertGreater(tgt.divergence, 0.4 * sc.fail_at,
                                   f"{unit} spoof gap too small")

    def test_other_units_do_not_fail(self):
        for unit in UNITS:
            with self.subTest(attacked=unit):
                b, s, _ = run_plant(attack_unit=unit, t_end=400.0)
                for other, u in s.units.items():
                    if other != unit:
                        self.assertFalse(
                            u.failed,
                            f"neighbour {other} failed during {unit} attack")


class TestHealthySisContrast(unittest.TestCase):
    """Manual drive with the SIS left healthy -> the targeted unit is held safe."""

    def test_manual_drive_is_caught(self):
        for unit in UNITS:
            with self.subTest(unit=unit):
                b, s, _ = run_plant(attack_unit=unit, bypass=False,
                                    t_end=400.0)
                tgt = s.units[unit]
                self.assertFalse(tgt.failed,
                                 f"{unit} failed despite a healthy SIS")
                self.assertTrue(tgt.trip_cmd,
                                f"{unit} SIS did not trip to hold it safe")


class TestForwardCoupling(unittest.TestCase):
    """Coupling propagates downstream only (tank -> reactor -> vessel)."""

    def test_tank_attack_warms_reactor(self):
        # Overfilling the tank feeds more reactant to the reactor, so the
        # reactor runs measurably hotter than its setpoint (but its healthy
        # control/SIS keep it below its own trip).
        b, s, _ = run_plant(attack_unit="tank", t_end=400.0)
        reactor = s.units["reactor"]
        sp = b.plant.unit("reactor").scenario.setpoint
        trip = b.plant.unit("reactor").scenario.trip_hh
        self.assertGreater(reactor.true_pv, sp + 5.0,
                           "reactor did not respond to the tank overfill")
        self.assertLess(reactor.true_pv, trip,
                        "reactor should stay below its own trip")
        self.assertFalse(reactor.failed)

    def test_downstream_attack_leaves_upstream_undisturbed(self):
        # Attacking the terminal vessel must not move the tank or reactor
        # (forward-only coupling; no back-pressure in Phase 1).
        b, s, _ = run_plant(attack_unit="vessel", t_end=400.0)
        for upstream in ("tank", "reactor"):
            with self.subTest(upstream=upstream):
                sp = b.plant.unit(upstream).scenario.setpoint
                u = s.units[upstream]
                self.assertLess(abs(u.true_pv - sp), 1.0,
                                f"{upstream} moved during a vessel attack")
                self.assertFalse(u.trip_cmd)


if __name__ == "__main__":
    unittest.main()
