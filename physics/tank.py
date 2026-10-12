"""Tank-overfill process model (Phase-0 anchor scenario).

Models one liquid storage tank as a single stirred lump with one state
variable: liquid level ``L`` (m). The governing law is the basic volume
balance (accumulation = in - out):

    dL/dt = (Q_in - Q_out) / A

where ``Q_in`` is the feed flow set by the feed valve and ``Q_out`` is the
(constant, Phase-0) downstream draw. See OVERVIEW.md / ARCHITECTURE.md.

Roles in the testbed:
  * The feed valve is the single actuator. The Control PLC (BPCS) modulates it
    to hold level at setpoint; the Safety PLC (SIS) high-level trip is supposed
    to force it shut before overflow. The attack defeats that trip.
  * This module is PURE PHYSICS: it integrates whatever flows result from the
    valve command it is given. It knows nothing about PLCs, Modbus, or the
    attack. Those live in other layers and act on it only through
    ``feed_valve``.

Level is clamped to ``[0, height_full]``: a real tank cannot hold more than its
height, so inflow beyond the brim becomes spill (tracked as ``spilled_volume``)
and the ``overflowing`` flag — the failure condition — latches True.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from common import scenarios
from common.scenarios import Scenario


@dataclass
class TankModel:
    """Single-ODE liquid level model for the tank-overfill scenario.

    All quantities are SI (m, m^2, m^3/s, s). Defaults give a slow, legible
    demo: at full feed the level rises 0.03 m/s, crossing the high-high trip
    (9 m) ~133 s and overflowing (10 m) ~167 s after a 5 m start.
    """

    # --- Geometry ---
    area: float = 1.0           # tank cross-sectional area A (m^2)
    height_full: float = 10.0   # brim: overflow at/above this level (m)

    # --- Flows ---
    q_in_max: float = 0.05      # feed flow at fully-open valve (m^3/s)
    q_out: float = 0.02         # constant downstream draw (m^3/s)

    # --- Operating / protection setpoints (used by PLC layer; informational here) ---
    level_setpoint: float = 5.0   # BPCS normal target (m)
    level_trip_hh: float = 9.0    # SIS high-high trip threshold (m)

    # --- Initial condition ---
    level0: float = 5.0         # initial level (m)

    # --- Scenario descriptor (for the uniform bridge/HMI/3D interface) ---
    scenario: Scenario = field(default_factory=lambda: scenarios.get("tank"))

    # --- State (set in __post_init__ / reset) ---
    level: float = field(init=False)
    spilled_volume: float = field(init=False)
    time: float = field(init=False)
    applied_feed: float = field(init=False)
    # Block/fill valve in series with the control valve. True = open (the
    # standalone default, so single-unit behaviour is unchanged). In the
    # connected plant the tank's SIS block-valve final element drives this, so a
    # trip physically shuts the feed even apart from the ``trip`` command.
    fill_open: bool = field(init=False)

    def __post_init__(self) -> None:
        self._validate()
        self.reset()

    def _validate(self) -> None:
        if self.area <= 0:
            raise ValueError("area must be > 0")
        if self.height_full <= 0:
            raise ValueError("height_full must be > 0")
        if self.q_in_max < 0 or self.q_out < 0:
            raise ValueError("flows must be >= 0")
        if not (0.0 <= self.level0 <= self.height_full):
            raise ValueError("level0 must be within [0, height_full]")

    def reset(self) -> None:
        """Return the model to its initial condition."""
        self.level = self.level0
        self.spilled_volume = 0.0
        self.time = 0.0
        self.applied_feed = 0.0
        self.fill_open = True

    # --- Derived quantities ---
    @property
    def volume(self) -> float:
        """Current liquid volume (m^3)."""
        return self.area * self.level

    @property
    def fraction(self) -> float:
        """Level as a fraction of full (0..1)."""
        return self.level / self.height_full

    @property
    def overflowing(self) -> bool:
        """True once the tank has reached its brim — the failure condition."""
        return self.level >= self.height_full

    # --- Uniform ProcessModel interface (shared with vessel/reactor) ---
    @property
    def pv(self) -> float:
        """Process variable for this scenario: the liquid level (m)."""
        return self.level

    @property
    def failed(self) -> bool:
        """Failure condition (alias of ``overflowing``)."""
        return self.overflowing

    def inflow(self, feed_valve: float) -> float:
        """Feed flow (m^3/s) for a given valve command, clamped to [0,1]."""
        return _clamp(feed_valve, 0.0, 1.0) * self.q_in_max

    # --- Integration ---
    def step(self, dt: float, feed_cmd: float, trip: bool = False) -> float:
        """Advance the model by ``dt`` seconds under the layer commands.

        ``feed_cmd`` is the BPCS feed-valve fraction in [0, 1] (values outside
        are clamped). ``trip`` is the SIS protective action and ``fill_open`` is
        the block/fill valve in series with it: a trip OR a shut block valve
        forces the feed to zero, so the effective feed is
        ``0 if (trip or not fill_open) else feed_cmd``. Returns the new level.
        Explicit Euler — exact for this constant-derivative ODE, so the result
        is timestep-independent away from the clamps.
        """
        if dt <= 0:
            raise ValueError("dt must be > 0")

        effective_feed = 0.0 if (trip or not self.fill_open) else feed_cmd
        self.applied_feed = _clamp(effective_feed, 0.0, 1.0)
        q_in = self.inflow(effective_feed)
        dLdt = (q_in - self.q_out) / self.area

        raw_level = self.level + dLdt * dt

        if raw_level >= self.height_full:
            # Tank is full: level pins at the brim and the excess spills.
            spill_height = raw_level - self.height_full
            self.spilled_volume += spill_height * self.area
            self.level = self.height_full
        elif raw_level <= 0.0:
            # Tank emptied: level pins at the floor.
            self.level = 0.0
        else:
            self.level = raw_level

        self.time += dt
        return self.level


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _demo() -> None:
    """Standalone sanity run: feed valve stuck open (protection OFF).

    Prints the trajectory and the time the true level crosses the SIS trip
    threshold and the overflow brim — the physics the attack will drive the
    tank to while the HMI is frozen at setpoint.
    """
    tank = TankModel()
    dt = 1.0
    t_trip = None
    t_overflow = None

    print(f"Tank overfill demo | area={tank.area} m^2  height_full={tank.height_full} m")
    print(f"q_in_max={tank.q_in_max}  q_out={tank.q_out} m^3/s  "
          f"setpoint={tank.level_setpoint} m  trip_hh={tank.level_trip_hh} m")
    print("Feed valve held fully OPEN (SIS trip defeated).\n")
    print(f"{'t (s)':>7} {'level (m)':>10} {'fraction':>9}  status")

    for _ in range(300):
        tank.step(dt, feed_cmd=1.0)
        if t_trip is None and tank.level >= tank.level_trip_hh:
            t_trip = tank.time
        if t_overflow is None and tank.overflowing:
            t_overflow = tank.time
        if tank.time % 20 == 0 or tank.overflowing:
            status = "OVERFLOW" if tank.overflowing else (
                "TRIP LIMIT" if tank.level >= tank.level_trip_hh else "")
            print(f"{tank.time:7.0f} {tank.level:10.3f} {tank.fraction:9.2%}  {status}")
        if tank.overflowing:
            break

    print()
    print(f"Crossed SIS trip threshold ({tank.level_trip_hh} m) at t = {t_trip:.0f} s")
    print(f"Reached overflow ({tank.height_full} m) at t = {t_overflow:.0f} s")
    print(f"Spilled volume after overflow step: {tank.spilled_volume:.3f} m^3")


if __name__ == "__main__":
    _demo()
