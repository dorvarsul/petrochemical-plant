"""Connected-plant model (Phase 1) — the three units as ONE flowsheet.

Phase 0.5 left three *independent* single-unit sims, run one at a time. Phase 1
couples them into one continuously-running plant, in process-flow order:

    T-101 feed tank  --feed-->  R-301 reactor (CSTR)  --product-->  V-201 vessel
      (level)                     (temperature)                     (pressure)
      overfill                    runaway                           overpressure

``PlantModel`` OWNS one ``TankModel``, one ``ReactorModel`` and one
``VesselModel`` (the exact Phase-0.5 models, unchanged) and advances all three
every ``step``. The only new physics is the FORWARD coupling between them:

  * **Tank -> Reactor.** A higher tank level means more head, so more feed flows
    to the reactor. That extra feed is added to the reactor's reactant
    throughput ``q`` (``reactor.q_coupling``): more reactant -> more reaction ->
    the reactor runs hotter. So overfilling the tank *stresses* the reactor.
  * **Reactor -> Vessel.** A hotter reactor product flashes more vapour into the
    downstream separation drum, so it is added to the vessel's pressure-rise
    (``vessel.p_coupling``): a reactor running hot *pressurises* the vessel.

Both couplings are written as a gain times the UPSTREAM unit's **deviation above
its own setpoint**, normalised by that setpoint:

    dev_up      = max(0, (pv_up - setpoint_up) / setpoint_up)
    q_coupling  = reactor.q   * G_TANK_REACTOR  * dev_tank      (reactant/s)
    p_coupling  = vessel.k_in * G_REACTOR_VESSEL * dev_reactor  (bar/s)

Calibrating to the *deviation* is the key Phase-1 design choice: at the baseline
operating point every unit sits at its setpoint, so every deviation is zero,
every coupling is zero, and the plant trivially holds a connected steady state
in which no SIS trips (the Phase-1 "done" criterion). The couplings only switch
on when an attack pushes a unit off its setpoint — and then they propagate
**forward**, downstream, which is exactly what makes a *connected* plant worth
building.

Scope / honesty (Phase 1 is plausible, not validated — see ROADMAP.md):

  * Coupling is **forward only** (tank->reactor->vessel). Back-pressure (a full
    downstream drum resisting upstream flow) is deliberately NOT modelled, so an
    attack on the *vessel* leaves the tank and reactor undisturbed. That is a
    documented simplification, not an oversight.
  * The gains are hand-tuned for a legible demo (an upstream attack visibly
    moves its downstream neighbour without instantly destroying it), not fitted
    to plant data. Validated coupling is the final OpenModelica phase.
  * The per-unit ``step(dt, feed_cmd, trip)`` interface is untouched; the
    couplings are injected through the neutral-by-default ``q_coupling`` /
    ``p_coupling`` attributes, so Phase 5 can still swap each unit's physics
    behind the same interface.
"""

from __future__ import annotations

from physics import make_model
from physics.reactor import ReactorModel
from physics.tank import TankModel
from physics.vessel import VesselModel

# --- Coupling gains (dimensionless; hand-tuned, see module docstring) -------
# Tank -> reactor: at full overfill (tank at its brim, dev = 1.0) the reactor's
# reactant feed is raised by this fraction. Kept modest so the reactor's own
# (healthy) control loop absorbs it with a small, visible temperature bump
# rather than being driven to its own trip.
G_TANK_REACTOR = 0.5
# Reactor -> vessel: scales the extra pressure-rise per unit of reactor
# temperature deviation. Sized so a full reactor runaway clearly over-pressures
# the downstream drum — enough that the vessel's own HEALTHY SIS trips and its
# relief valve holds it safe, demonstrating defence-in-depth on the neighbour
# while the attacked unit (whose SIS is overridden) runs to failure.
G_REACTOR_VESSEL = 1.0

# Plant flow order (upstream -> downstream). The single source of unit order.
FLOW = ("tank", "reactor", "vessel")


def _dev_above_setpoint(pv: float, setpoint: float) -> float:
    """Upstream deviation ABOVE its own setpoint, normalised, clamped >= 0.

    Only an upstream unit pushed *above* setpoint stresses its downstream
    neighbour; at or below setpoint the coupling is zero (baseline steady
    state). Returning a clamped, normalised fraction keeps the gains unitless.
    """
    if setpoint <= 0:
        return 0.0
    dev = (pv - setpoint) / setpoint
    return dev if dev > 0.0 else 0.0


class PlantModel:
    """The three units wired as one connected, continuously-running plant.

    Build it with no arguments; it constructs each unit from its scenario
    (``common/scenarios.py`` stays the single source of per-unit truth). Step it
    with per-unit ``(feed_cmd, trip)`` commands — one BPCS actuator fraction and
    one SIS trip per unit — and it advances every unit by ``dt`` with the
    forward couplings applied. Read per-unit ground truth back through
    ``snapshot()`` or the ``tank`` / ``reactor`` / ``vessel`` attributes.
    """

    def __init__(self) -> None:
        self.tank: TankModel = make_model("tank")      # type: ignore[assignment]
        self.reactor: ReactorModel = make_model("reactor")  # type: ignore[assignment]
        self.vessel: VesselModel = make_model("vessel")     # type: ignore[assignment]
        self._by_id = {
            "tank": self.tank,
            "reactor": self.reactor,
            "vessel": self.vessel,
        }
        # Last couplings applied, exposed for the HMI/3D and tests.
        self.q_coupling = 0.0
        self.p_coupling = 0.0
        self.time = 0.0

    # --- unit access ---------------------------------------------------------
    @property
    def units(self):
        """Unit (id, model) pairs in flow order."""
        return [(uid, self._by_id[uid]) for uid in FLOW]

    def unit(self, unit_id: str):
        """The physics model for one unit id."""
        try:
            return self._by_id[unit_id]
        except KeyError:
            raise KeyError(
                f"unknown unit {unit_id!r}; choose one of {list(FLOW)}"
            ) from None

    # --- lifecycle -----------------------------------------------------------
    def reset(self) -> None:
        """Return every unit to its initial condition and clear couplings."""
        for _, m in self.units:
            m.reset()
        self.q_coupling = 0.0
        self.p_coupling = 0.0
        self.time = 0.0

    # --- integration ---------------------------------------------------------
    def step(self, dt: float, commands: dict) -> None:
        """Advance the whole plant by ``dt`` under per-unit layer commands.

        ``commands`` maps each unit id to a ``(feed_cmd, trip)`` pair:
        ``feed_cmd`` is that unit's BPCS actuator fraction in [0,1], ``trip`` is
        its SIS protective action. Missing units default to ``(0.0, False)``.

        Couplings are computed from the CURRENT (pre-step) upstream state and
        injected into the downstream units before stepping, so influence
        propagates one unit per cycle in flow order — the normal explicit
        coupling of a staged flowsheet at the bridge's ``dt``.
        """
        if dt <= 0:
            raise ValueError("dt must be > 0")

        # 1. Forward couplings from the current upstream deviations.
        dev_tank = _dev_above_setpoint(self.tank.pv, self.tank.scenario.setpoint)
        dev_reactor = _dev_above_setpoint(
            self.reactor.pv, self.reactor.scenario.setpoint)
        self.q_coupling = self.reactor.q * G_TANK_REACTOR * dev_tank
        self.p_coupling = self.vessel.k_in * G_REACTOR_VESSEL * dev_reactor
        self.reactor.q_coupling = self.q_coupling
        self.vessel.p_coupling = self.p_coupling

        # 2. Step each unit with its own (feed_cmd, trip).
        for uid, m in self.units:
            feed_cmd, trip = commands.get(uid, (0.0, False))
            m.step(dt, feed_cmd=feed_cmd, trip=bool(trip))

        self.time += dt

    # --- read-outs -----------------------------------------------------------
    @property
    def any_failed(self) -> bool:
        return any(m.failed for _, m in self.units)

    @property
    def failed_units(self) -> list:
        return [uid for uid, m in self.units if m.failed]

    def snapshot(self) -> dict:
        """Per-unit ground-truth snapshot (for the HMI/3D view and tests)."""
        out = {"time": self.time,
               "q_coupling": self.q_coupling, "p_coupling": self.p_coupling,
               "units": {}}
        for uid, m in self.units:
            sc = m.scenario
            out["units"][uid] = {
                "tag": sc.unit_tag,
                "pv_name": sc.pv_name,
                "pv": m.pv,
                "pv_units": sc.pv_units,
                "fraction": m.fraction,
                "failed": m.failed,
                "applied_feed": m.applied_feed,
            }
        return out
