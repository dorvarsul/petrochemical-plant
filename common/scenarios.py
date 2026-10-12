"""Scenario registry — the single source of truth for the three scenarios.

Phase 0 anchored on ONE scenario (tank overfill). This module generalizes the
testbed to all three damage scenarios from OVERVIEW.md while staying on Python
ODE physics (no OpenModelica — that is still a later phase):

    tank    — tank overfill    → high-level trip   (1 ODE: level)
    vessel  — vessel overpressure → relief valve    (1 ODE: pressure)
    reactor — exothermic runaway  → cooling trip     (2 ODEs: temp + conc.)

All three share ONE control/attack topology, which is why they can be
parameterized rather than forked:

  * a measured process variable (PV) that rises toward danger,
  * a BPCS actuator the control PLC modulates with a proportional law to hold
    the PV at its writable SETPOINT (feed valve / compressor / coolant valve),
  * an SIS high-high trip that, when healthy, forces a protective action and
    holds the PV safe,
  * the attack (external, over Modbus): the attacker writes a malicious
    SETPOINT so the BPCS drives the process toward failure, AND sets
    SIS_OVERRIDE so the trip is suppressed and the operator's PV display
    freezes. With no attacker the BPCS simply holds setpoint — the baseline
    runs forever and never trips.

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

    # --- BPCS control law (integer, register units) --------------------------
    # The Control PLC runs one proportional law for every scenario:
    #     feed_cmd[per-mille] = clamp(bias_pm + kp_num*(SP - PV)/kp_den, 0, 1000)
    # where SP and PV are register-unit integers and feed_cmd is the actuator
    # position. ``kp_num`` is SIGNED: positive where opening the actuator raises
    # the PV (tank feed valve, vessel compressor), negative for the reactor,
    # whose actuator is the COOLANT valve — opening it lowers the temperature, so
    # the controller opens it when the PV is ABOVE setpoint. The attacker drives
    # the process by writing a malicious SP (``attack_setpoint_reg``): it
    # saturates feed_cmd toward failure (valves wide open / coolant shut).

    # --- human-readable wording (HMI / 3D / docs) ---
    actuator: str        # BPCS actuator, e.g. "feed valve"
    trip_action: str     # SIS protective action, e.g. "force feed shut"
    failure_word: str    # "OVERFLOW" | "RUPTURE" | "RUNAWAY"
    rising_blurb: str    # one line describing the hazard for the HMI legend

    # --- physics coefficients (consumed by the matching physics model) ---
    physics: Mapping[str, float] = field(default_factory=dict)

    # --- BPCS control-law constants (see block above; register units) ---
    ctrl_bias_pm: int = 0    # feed_cmd at zero error (per-mille)
    ctrl_kp_num: int = 0     # proportional-gain numerator (signed)
    ctrl_kp_den: int = 1     # proportional-gain denominator (> 0)

    # --- real-SIF realism (process units; see the ladder below) -------------
    # The operator may only set the BPCS SETPOINT within [sp_lo, sp_hi]; a write
    # outside that band is clamped by the control PLC (which defeats the old
    # off-scale-setpoint attack — hence the evolved manual-mode attack). The SIS
    # gains a pre-alarm "H" below its "HH" trip, and a manual reset that only
    # clears the latch once the PV has fallen a deadband below the trip. The
    # escalation ladder for each scenario is:
    #     sp_lo  <  (SP range)  <  sp_hi == pre_alarm_h  <  trip_hh  <  fail_at
    # (the reactor is the one exception — its ignition point sits at the trip, so
    #  sp_hi < pre_alarm_h < trip_hh; see the per-scenario values.)
    sp_lo: float = 0.0           # operator setpoint clamp — low  (process units)
    sp_hi: float = 0.0           # operator setpoint clamp — high (process units)
    pre_alarm_h: float = 0.0     # SIS "H" pre-alarm, below the "HH" trip
    reset_deadband: float = 0.0  # reset permissive margin below trip_hh

    # --- derived ---
    @property
    def setpoint_reg(self) -> int:
        """BPCS normal operating setpoint as an integer register value.

        Written into the BPCS ``SETPOINT`` memory word at startup/reset as the
        operator's safe target. The attacker overwrites it with
        ``attack_setpoint_reg`` over Modbus — that is the whole attack driver.
        """
        return int(round(self.setpoint * self.reg_scale))

    @property
    def attack_setpoint_reg(self) -> int:
        """The malicious operating setpoint an external attacker writes to the
        BPCS: twice the gauge full scale — an off-scale-high demand (e.g. 20 m
        in a 10 m tank). It keeps the control actuator saturated (feed valves
        wide open / the coolant valve shut) all the way PAST failure, instead of
        the loop easing off as the PV nears an in-range target. Kept well within
        a signed 16-bit register."""
        return min(2 * self.reg_full_scale, 32000)

    @property
    def attack_manual_out_reg(self) -> int:
        """The dangerous actuator position the evolved attack forces in BPCS
        MANUAL mode (per-mille). Where opening the actuator raises the PV
        (tank feed valve, vessel compressor; ``ctrl_kp_num > 0``) the attacker
        drives it wide open (1000); for the reactor (coolant valve, negative
        gain) it drives it shut (0) to remove cooling. Manual mode bypasses the
        control law, so the setpoint clamp cannot stop it."""
        return 1000 if self.ctrl_kp_num > 0 else 0

    @property
    def sp_lo_reg(self) -> int:
        """Operator setpoint-clamp low bound as an integer register value."""
        return int(round(self.sp_lo * self.reg_scale))

    @property
    def sp_hi_reg(self) -> int:
        """Operator setpoint-clamp high bound as an integer register value."""
        return int(round(self.sp_hi * self.reg_scale))

    @property
    def pre_alarm_h_reg(self) -> int:
        """SIS "H" pre-alarm threshold as an integer register value."""
        return int(round(self.pre_alarm_h * self.reg_scale))

    @property
    def reset_db_reg(self) -> int:
        """SIS reset deadband as an integer register value (reset permits only
        when PV < trip_reg - reset_db_reg)."""
        return int(round(self.reset_deadband * self.reg_scale))

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


# The BPCS actuator may move at most this many per-mille per 100 ms scan — a
# realistic output rate limit. Fixed (not per-scenario) and small enough that it
# never disturbs the tuned baseline (startup peak == setpoint for all three),
# while bounding how fast a MANUAL→AUTO switch or an attack can slew the valve.
CTRL_RATE_LIM_PM: int = 100


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
        # A steady downstream draw (q_out) the feed valve must replace, so the
        # BPCS level loop has a real operating point (feed ≈ 40% holds 5 m).
        physics=dict(area=1.0, q_in_max=0.05, q_out=0.02),
        # feed = 0.40 + 0.60*(SP-L)[m]; holds 5 m, feed→100% when SP driven high.
        ctrl_bias_pm=400, ctrl_kp_num=3, ctrl_kp_den=5,
        # ladder: 1 < SP < 8 == H ; 8 < 9 (HH) < 10 (fail). Clamped SP (8 m)
        # settles below trip → a setpoint-tamper alone can never overfill.
        sp_lo=1.0, sp_hi=8.0, pre_alarm_h=8.5, reset_deadband=0.5,
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
        # feed = 0.20 + 0.30*(SP-P)[bar]; holds 5 bar, feed→100% when SP high.
        ctrl_bias_pm=200, ctrl_kp_num=3, ctrl_kp_den=1,
        # ladder: 2 < SP < 14 ; 15 (H) < 16 (HH) < 20 (fail). Clamped SP (14 bar)
        # settles below trip → a setpoint-tamper alone can never rupture.
        sp_lo=2.0, sp_hi=14.0, pre_alarm_h=15.0, reset_deadband=1.0,
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
        actuator="coolant valve",
        trip_action="emergency max cooling",
        failure_word="RUNAWAY",
        rising_blurb="exothermic reaction self-heating toward runaway",
        # CSTR with Arrhenius self-heating. Reactant throughput is FIXED; the
        # BPCS temperature loop modulates the COOLANT VALVE (feed_cmd = valve
        # fraction u) to hold 80 °C. This is the one scenario whose actuator
        # REMOVES energy, so its control gain is negative (open the coolant
        # valve when too hot).
        #   k(T)  = k0*exp(-E/(T+273.15));   r = k(T)*C
        #   dC/dt = q*(C_in - C) - r         (q fixed)
        #   dT/dt = beta*r - cool*(T - Tc)
        #   cool  = cool_trip (SIS emergency) else cool_min + u*(cool_ctrl-cool_min)
        # Tuned for a robust baseline (u≈50% coolant holds 80 °C) and a legible
        # attack: a malicious high setpoint shuts the coolant valve (u→0,
        # cool→cool_min), giving a slow induction then an excursion to 300 °C by
        # ~150 s; the SIS trip at 110 °C forces cool_trip and quenches it safely.
        # Time-dilated ×0.35 vs. the bench kinetics for watch-ability (shape and
        # steady states preserved — k0/cooling/q scale together).
        physics=dict(
            k0=23275.0, E=6000.0, beta=500.0,
            q=0.00147, C_in=1.0, C0=0.6,
            cool_min=0.0035, cool_ctrl=0.007105, cool_trip=0.098, Tc=25.0,
        ),
        # u = 0.50 - 0.04*(SP-T)[°C]; holds 80 °C at ~50% coolant, u→0% when SP high.
        ctrl_bias_pm=500, ctrl_kp_num=-4, ctrl_kp_den=1,
        # ladder (reactor exception): 50 < SP < 100 ; 105 (H) < 110 (HH) < 300
        # (fail). The exotherm reaches ~110 °C even at the clamped setpoint, so a
        # setpoint-tamper is caught by the HEALTHY SIS (trip, no failure) rather
        # than held below trip — the uniform invariant is only "tamper never
        # fails", realized here by the trip instead of by the clamp alone.
        sp_lo=50.0, sp_hi=100.0, pre_alarm_h=105.0, reset_deadband=5.0,
    ),
}

# ---------------------------------------------------------------------------
# P-401 product-storage tank — the terminal SINK of the flowsheet
# (V-201 bottoms -> P-401 -> export). It is NOT a scenario: it has no BPCS/SIS
# pair and is never an attack target, so it is a plain parameter block rather
# than a Scenario. It carries a single level ODE and alarm thresholds; its fill
# comes from the (steady) V-201 bottoms draw and it empties through an
# operator-commanded EXPORT valve. q_export == q_fill is deliberate: with EXPORT
# open the level holds (dL/dt = 0, no baseline drift); with EXPORT shut it fills
# to HI then HH. LO/LL become reachable only if the fill is reduced.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProductStorage:
    tag: str = "P-401"
    pv_name: str = "level"
    pv_units: str = "m"
    reg_scale: int = 1000          # reg = round(level_m * 1000)  (mm)
    area: float = 1.0              # tank cross-section (m^2)
    height_full: float = 6.0       # brim (m); gauge/3D full scale
    level0: float = 3.0            # initial / reset level (m)
    q_fill: float = 0.04           # steady V-201 bottoms inflow (m^3/s)
    q_export: float = 0.04         # draw when EXPORT open (== q_fill → holds)
    ll: float = 0.5                # low-low level alarm (m)
    lo: float = 1.0                # low level alarm (m)
    hi: float = 5.0                # high level alarm (m)
    hh: float = 5.5                # high-high level alarm (m)

    @property
    def reg_full_scale(self) -> int:
        return int(round(self.height_full * self.reg_scale))

    def pv_to_reg(self, level: float) -> int:
        raw = round(level * self.reg_scale)
        return int(_clamp(raw, 0, self.reg_full_scale))

    def fraction(self, level: float) -> float:
        return _clamp(level / self.height_full, 0.0, 1.0)


PRODUCT = ProductStorage()


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
