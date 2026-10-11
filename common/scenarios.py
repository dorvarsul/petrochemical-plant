"""Scenario registry — the single source of truth for the three scenarios.

Phase 0 anchored on ONE scenario (tank overfill). This module generalizes the
testbed to all three damage scenarios from OVERVIEW.md while staying on Python
ODE physics (no OpenModelica — that is still a later phase):

    tank    — tank overfill    → high-level trip   (1 ODE: level)
    vessel  — vessel overpressure → relief valve    (1 ODE: pressure)
    reactor — exothermic runaway  → cooling trip     (2 ODEs: temp + conc.)

All three share ONE control/attack topology, which is why they can be
parameterized rather than forked:

  * a measured process variable (PV) that RISES toward danger,
  * a BPCS actuator the control PLC drives (in Phase 0, held wide open) which
    pushes the PV up,
  * an SIS high-high trip that, when healthy, forces a protective action and
    holds the PV safe,
  * the attack: SIS_OVERRIDE suppresses the trip and freezes the operator's PV
    display while the true PV runs past the failure threshold.

Everything that differs per scenario lives HERE: PV meaning/units, the integer
Modbus scaling, the BPCS/SIS setpoints, the failure threshold, the physics
coefficients, and the human-readable wording used by the HMI / 3D view / docs.
The register MAP (addresses, topology) is identical across scenarios — only the
interpretation of the PV register changes — so ``common/registers.py`` reads its
per-scenario scaling and trip threshold from this module.

Integer Modbus encoding: ``reg = round(pv * reg_scale)``, chosen per scenario so
full scale lands at a clean value well under the 16-bit ceiling:

    tank    level mm     (×1000)  10.0 m   -> 10000
    vessel  pressure cbar (×100)  20.0 bar -> 2000
    reactor temp ddegC   (×10)    300  degC -> 3000

By convention ``pv_full_scale == fail_at`` for every scenario, so the HMI gauge
and 3D fill read "full = failure".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Scenario:
    """Everything that varies between the three scenarios."""

    # --- identity ---
    id: str          # "tank" | "vessel" | "reactor"  (the key everywhere)
    title: str       # "Tank overfill"
    unit_tag: str    # plant unit designation, e.g. "T-101"

    # --- process variable (the rising measurement) ---
    pv_name: str         # "level" | "pressure" | "temperature"
    pv_symbol: str       # "L" | "P" | "T"
    pv_units: str        # display units: "m" | "bar" | "°C"
    reg_units: str       # integer register units: "mm" | "cbar" | "ddegC"
    reg_scale: int       # reg = round(pv * reg_scale)
    pv0: float           # initial PV (process units)
    setpoint: float      # BPCS nominal operating target (process units)
    trip_hh: float       # SIS high-high trip threshold (process units)
    fail_at: float       # failure threshold == gauge full scale (process units)

    # --- human-readable wording (HMI / 3D / docs) ---
    actuator: str        # BPCS actuator, e.g. "feed valve"
    trip_action: str     # SIS protective action, e.g. "force feed shut"
    failure_word: str    # "OVERFLOW" | "RUPTURE" | "RUNAWAY"
    rising_blurb: str    # one line describing the hazard for the HMI legend

    # --- physics coefficients (consumed by the matching physics model) ---
    physics: Mapping[str, float] = field(default_factory=dict)

    # --- derived ---
    @property
    def pv_full_scale(self) -> float:
        """Top of the PV range == the failure threshold (gauge/3D brim)."""
        return self.fail_at

    @property
    def reg_full_scale(self) -> int:
        """PV full scale as an integer register value."""
        return int(round(self.fail_at * self.reg_scale))

    @property
    def trip_reg(self) -> int:
        """High-high trip threshold as an integer register value.

        This is the constant compiled into the matching SIS ST program
        (``TRIP_HH``) and mirrored by the fake SIS in the bridge.
        """
        return int(round(self.trip_hh * self.reg_scale))

    def pv_to_reg(self, pv: float) -> int:
        """Process value -> integer Modbus register, clamped to [0, full]."""
        raw = round(pv * self.reg_scale)
        return int(_clamp(raw, 0, self.reg_full_scale))

    def reg_to_pv(self, reg: int) -> float:
        """Integer Modbus register -> process value."""
        return reg / self.reg_scale

    def fraction(self, pv: float) -> float:
        """PV as a 0..1 fraction of full scale (for the gauge / 3D fill)."""
        return _clamp(pv / self.pv_full_scale, 0.0, 1.0)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


# ---------------------------------------------------------------------------
# The three scenarios. Coefficients are tuned for a LEGIBLE demo (slow, ~2-3 min
# to failure at full drive; the trip comfortably holds the process safe), not
# for plant fidelity — that is an explicit later-phase goal (OVERVIEW.md).
# ---------------------------------------------------------------------------
SCENARIOS: dict[str, Scenario] = {
    "tank": Scenario(
        id="tank",
        title="Tank overfill",
        unit_tag="T-101",
        pv_name="level",
        pv_symbol="L",
        pv_units="m",
        reg_units="mm",
        reg_scale=1000,
        pv0=5.0,
        setpoint=5.0,
        trip_hh=9.0,
        fail_at=10.0,
        actuator="feed valve",
        trip_action="force feed shut",
        failure_word="OVERFLOW",
        rising_blurb="feed open, level rising toward the brim",
        physics=dict(area=1.0, q_in_max=0.05, q_out=0.0),
    ),
    "vessel": Scenario(
        id="vessel",
        title="Vessel overpressure",
        unit_tag="V-201",
        pv_name="pressure",
        pv_symbol="P",
        pv_units="bar",
        reg_units="cbar",
        reg_scale=100,
        pv0=5.0,
        setpoint=5.0,
        trip_hh=16.0,
        fail_at=20.0,
        actuator="compressor feed",
        trip_action="open relief valve",
        failure_word="RUPTURE",
        rising_blurb="compressor feeding, pressure climbing toward rupture",
        # dP/dt = k_in*feed - k_out - k_relief*trip   (bar/s)
        physics=dict(k_in=0.10, k_out=0.02, k_relief=0.30),
    ),
    "reactor": Scenario(
        id="reactor",
        title="Reactor runaway",
        unit_tag="R-301",
        pv_name="temperature",
        pv_symbol="T",
        pv_units="°C",
        reg_units="ddegC",
        reg_scale=10,
        pv0=80.0,
        setpoint=80.0,
        trip_hh=110.0,
        fail_at=300.0,
        actuator="reactant feed",
        trip_action="force max cooling",
        failure_word="RUNAWAY",
        rising_blurb="exothermic reaction self-heating toward runaway",
        # CSTR with Arrhenius self-heating; cooling jacket base vs. tripped-max.
        #   k(T) = k0*exp(-E/(T+273.15))
        #   r    = k(T)*C
        #   dC/dt = q*(C_in - C) - r      (q = feed*F_over_V)
        #   dT/dt = beta*r - cool*(T - Tc)
        #   cool  = cool_max if trip else cool_base
        # Coefficients tuned (see repo history) for a legible runaway: a slow
        # accelerating induction (80->~97 °C over ~135 s) then the excursion to
        # 300 °C by ~158 s with base cooling; the SIS trip at 110 °C quenches it
        # to a safe peak ~110 °C, and max cooling from the start holds it at 80.
        physics=dict(
            k0=66500.0, E=6000.0, beta=500.0,
            F_over_V=0.0042, C_in=1.0, C0=0.6,
            cool_base=0.0149, cool_max=0.28, Tc=25.0,
        ),
    ),
}

DEFAULT_SCENARIO = "tank"


def get(scenario_id: str) -> Scenario:
    """Look up a scenario by id, with a clear error for a bad key."""
    try:
        return SCENARIOS[scenario_id]
    except KeyError:
        raise KeyError(
            f"unknown scenario {scenario_id!r}; "
            f"choose one of {sorted(SCENARIOS)}"
        ) from None


def ids() -> list[str]:
    """Scenario ids in a stable display order."""
    return ["tank", "vessel", "reactor"]
