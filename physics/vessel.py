"""Vessel-overpressure process model (single ODE, pressure).

One pressurized vessel as a single lump with one state variable: gauge pressure
``P`` (bar). A compressor feeds it (the BPCS actuator, modulated by the control
loop to hold setpoint); a constant process draw bleeds it; a relief valve is the
protective device.

    dP/dt = k_in * feed - k_out - k_relief * trip        (bar/s)

  * ``feed`` ∈ [0,1] is the compressor feed the BPCS commands. At the baseline the
    control loop trims it so ``k_in*feed == k_out`` (steady at setpoint); the
    attack's off-scale setpoint drives it wide open, so ``k_in - k_out`` is
    positive and the pressure climbs toward rupture.
  * ``trip`` is the SIS high-pressure interlock. When healthy it opens the relief
    valve (``k_relief`` large enough to overpower the feed), so pressure falls
    and the vessel stays safe. The attack suppresses ``trip`` — the relief never
    opens and the vessel runs to rupture while the HMI shows nominal pressure.

Roles mirror the tank: the compressor feed is the single BPCS actuator, the
relief valve is the SIS protective action, and this module integrates whatever
commands the bridge hands it. Pressure is clamped to ``[0, fail_at]``; reaching
``fail_at`` latches the ``ruptured`` failure flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from common import scenarios
from common.scenarios import Scenario
from physics.base import clamp


@dataclass
class VesselModel:
    """Single-ODE pressure model for the vessel-overpressure scenario."""

    scenario: Scenario = field(
        default_factory=lambda: scenarios.get("vessel"))

    # State (set in __post_init__ / reset)
    pressure: float = field(init=False)
    time: float = field(init=False)
    applied_feed: float = field(init=False)
    _ruptured: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        p = self.scenario.physics
        self.k_in = float(p["k_in"])
        self.k_out = float(p["k_out"])
        self.k_relief = float(p["k_relief"])
        self._fail = self.scenario.fail_at
        self.reset()

    def reset(self) -> None:
        self.pressure = self.scenario.pv0
        self.time = 0.0
        self.applied_feed = 0.0
        self._ruptured = False

    # --- uniform read-outs ---
    @property
    def pv(self) -> float:
        return self.pressure

    @property
    def fraction(self) -> float:
        return self.scenario.fraction(self.pressure)

    @property
    def failed(self) -> bool:
        return self._ruptured

    @property
    def ruptured(self) -> bool:
        return self._ruptured

    # --- integration ---
    def step(self, dt: float, feed_cmd: float, trip: bool = False) -> float:
        if dt <= 0:
            raise ValueError("dt must be > 0")
        feed = clamp(feed_cmd, 0.0, 1.0)
        self.applied_feed = feed

        relief = self.k_relief if trip else 0.0
        dPdt = self.k_in * feed - self.k_out - relief
        raw = self.pressure + dPdt * dt

        if raw >= self._fail:
            self.pressure = self._fail
            self._ruptured = True
        elif raw <= 0.0:
            self.pressure = 0.0
        else:
            self.pressure = raw

        self.time += dt
        return self.pressure


def _demo() -> None:
    """Standalone sanity run: compressor open, relief defeated (no trip)."""
    v = VesselModel()
    s = v.scenario
    dt = 1.0
    t_trip = t_fail = None
    print(f"Vessel overpressure demo | {s.unit_tag}  trip_hh={s.trip_hh} bar  "
          f"rupture={s.fail_at} bar  (relief defeated)\n")
    print(f"{'t (s)':>7} {'P (bar)':>9} {'frac':>7}  status")
    for _ in range(400):
        v.step(dt, feed_cmd=1.0, trip=False)
        if t_trip is None and v.pressure >= s.trip_hh:
            t_trip = v.time
        if t_fail is None and v.ruptured:
            t_fail = v.time
        if v.time % 20 == 0 or v.ruptured:
            tag = s.failure_word if v.ruptured else (
                "TRIP LIMIT" if v.pressure >= s.trip_hh else "")
            print(f"{v.time:7.0f} {v.pressure:9.3f} {v.fraction:7.2%}  {tag}")
        if v.ruptured:
            break
    print(f"\nCrossed trip ({s.trip_hh} bar) at t={t_trip:.0f}s; "
          f"rupture ({s.fail_at} bar) at t={t_fail:.0f}s")


if __name__ == "__main__":
    _demo()
