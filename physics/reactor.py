"""Reactor-runaway process model (two coupled ODEs, Arrhenius feedback).

A continuous stirred-tank reactor (CSTR) running one exothermic reaction
A → products. Two state variables: temperature ``T`` (°C) and reactant
concentration ``C`` (mol/L). The Arrhenius rate rises with temperature, so the
heat it releases feeds back into the temperature — the runaway mechanism.

    k(T)   = k0 * exp(-E / (T + 273.15))        reaction rate constant
    r      = k(T) * C                            reaction rate (mol/L/s)
    dC/dt  = q * (C_in - C) - r                  q fixed (reactant throughput)
    dT/dt  = beta * r - cool * (T - Tc)

Unlike the tank/vessel (whose BPCS actuator ADDS to the PV), the reactor's
actuator REMOVES energy. ``feed_cmd`` here is the **coolant-valve fraction**
``u`` ∈ [0,1] the BPCS temperature loop modulates, and the reactant throughput
is fixed:

    cool = cool_trip (SIS emergency)  else  cool_min + u*(cool_ctrl - cool_min)

  * At the baseline the BPCS holds ``u`` ≈ 0.5 (coolant valve half open), which
    balances the exotherm at the 80 °C setpoint.
  * The attack writes a malicious high temperature setpoint, so the controller
    (believing the reactor is far too cold) drives ``u`` → 0 — the coolant valve
    shuts, cooling falls to ``cool_min``, and the exotherm runs away.
  * ``trip`` is the SIS high-temperature interlock: it forces ``cool_trip``
    (emergency cooling, far above the control range), overpowering the exotherm
    and pulling the temperature back down. The attack suppresses ``trip`` — so
    emergency cooling never engages and the reactor runs to failure while the
    HMI shows a nominal temperature.

Temperature is clamped to ``[Tc, fail_at]``; reaching ``fail_at`` latches the
``runaway`` failure flag. Integrated with a small sub-stepped explicit Euler so
the stiff Arrhenius feedback stays stable at the bridge's coarse ``dt``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from common import scenarios
from common.scenarios import Scenario
from physics.base import clamp

_SUBSTEPS = 20  # internal Euler sub-steps per bridge step (stiffness margin)


@dataclass
class ReactorModel:
    """Two-ODE CSTR model for the exothermic-runaway scenario."""

    scenario: Scenario = field(
        default_factory=lambda: scenarios.get("reactor"))

    # State (set in __post_init__ / reset)
    temperature: float = field(init=False)
    concentration: float = field(init=False)
    time: float = field(init=False)
    applied_feed: float = field(init=False)
    _runaway: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        p = self.scenario.physics
        self.k0 = float(p["k0"])
        self.E = float(p["E"])
        self.beta = float(p["beta"])
        self.q = float(p["q"])                 # fixed reactant throughput
        self.C_in = float(p["C_in"])
        self.C0 = float(p["C0"])
        self.cool_min = float(p["cool_min"])   # coolant valve shut (u=0): runaway
        self.cool_ctrl = float(p["cool_ctrl"]) # coolant valve full (u=1): normal max
        self.cool_trip = float(p["cool_trip"]) # SIS emergency cooling (overrides u)
        self.Tc = float(p["Tc"])
        self._fail = self.scenario.fail_at
        self.reset()

    def reset(self) -> None:
        self.temperature = self.scenario.pv0
        self.concentration = self.C0
        self.time = 0.0
        self.applied_feed = 0.0
        self._runaway = False

    # --- uniform read-outs ---
    @property
    def pv(self) -> float:
        return self.temperature

    @property
    def fraction(self) -> float:
        return self.scenario.fraction(self.temperature)

    @property
    def failed(self) -> bool:
        return self._runaway

    @property
    def runaway(self) -> bool:
        return self._runaway

    # --- reaction kinetics ---
    def rate(self, T: float, C: float) -> float:
        """Arrhenius reaction rate r = k0*exp(-E/(T+273.15))*C (mol/L/s)."""
        k = self.k0 * math.exp(-self.E / (T + 273.15))
        return k * C

    # --- integration (sub-stepped explicit Euler) ---
    def step(self, dt: float, feed_cmd: float, trip: bool = False) -> float:
        if dt <= 0:
            raise ValueError("dt must be > 0")
        u = clamp(feed_cmd, 0.0, 1.0)  # coolant-valve fraction from the BPCS
        # Display the actuator the operator acts on: the coolant valve. On an
        # SIS trip the emergency cooling runs the jacket wide open.
        self.applied_feed = 1.0 if trip else u

        q = self.q  # reactant throughput is fixed; the BPCS controls cooling
        cool = self.cool_trip if trip else self.cool_min + u * (self.cool_ctrl - self.cool_min)
        h = dt / _SUBSTEPS

        T, C = self.temperature, self.concentration
        for _ in range(_SUBSTEPS):
            r = self.rate(T, C)
            dC = q * (self.C_in - C) - r
            dT = self.beta * r - cool * (T - self.Tc)
            T += dT * h
            C += dC * h
            if C < 0.0:
                C = 0.0
            if T <= self.Tc:
                T = self.Tc
            if T >= self._fail:
                T = self._fail
                self._runaway = True
                break

        self.temperature = T
        self.concentration = C
        self.time += dt
        return self.temperature


def _demo() -> None:
    """Standalone sanity run: coolant valve shut (attack), SIS trip defeated."""
    rx = ReactorModel()
    s = rx.scenario
    dt = 1.0
    t_trip = t_fail = None
    print(f"Reactor runaway demo | {s.unit_tag}  trip_hh={s.trip_hh} °C  "
          f"runaway={s.fail_at} °C  (coolant valve shut, SIS trip defeated)\n")
    print(f"{'t (s)':>7} {'T (°C)':>9} {'C (mol/L)':>10} {'frac':>7}  status")
    for _ in range(600):
        rx.step(dt, feed_cmd=0.0, trip=False)
        if t_trip is None and rx.temperature >= s.trip_hh:
            t_trip = rx.time
        if t_fail is None and rx.runaway:
            t_fail = rx.time
        if rx.time % 20 == 0 or rx.runaway:
            tag = s.failure_word if rx.runaway else (
                "TRIP LIMIT" if rx.temperature >= s.trip_hh else "")
            print(f"{rx.time:7.0f} {rx.temperature:9.3f} "
                  f"{rx.concentration:10.4f} {rx.fraction:7.2%}  {tag}")
        if rx.runaway:
            break
    tt = f"{t_trip:.0f}s" if t_trip else "n/a"
    tf = f"{t_fail:.0f}s" if t_fail else "n/a"
    print(f"\nCrossed trip ({s.trip_hh} °C) at t={tt}; "
          f"runaway ({s.fail_at} °C) at t={tf}")


if __name__ == "__main__":
    _demo()
