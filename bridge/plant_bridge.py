"""Connected-plant bridge (Phase 1) — one BPCS + one SIS per unit.

The Phase-0 ``Bridge`` couples ONE physics model to ONE Control PLC + ONE Safety
PLC. The connected plant runs all three units at once, so ``PlantBridge`` couples
a :class:`physics.plant.PlantModel` to *three* control/safety pairs — one BPCS
and one SIS per unit (tank, reactor, vessel) — and drives them with the exact
same per-unit logic the single-unit bridge uses.

Each cycle, for every unit:

  1. publish the unit's true PV to its BPCS and SIS (sensor injection),
  2. let that unit's two PLCs scan,
  3. read back its actuator command (BPCS) and trip (SIS),

then step the **whole plant once** with all three ``(feed_cmd, trip)`` pairs, so
the forward couplings between units (see :mod:`physics.plant`) are applied in a
single consistent integration step.

The fake path (``make_fake_plant_bridge``) uses the in-memory ``FakeBpcs`` /
``FakeSis`` — each PLC instance owns its own register space, so the three units'
identical register addresses never collide. This is the dependency-free way to
exercise the connected plant end to end, mirroring how the Phase-0.5 acceptance
runs on fake PLCs. Extending the LIVE OpenPLC stack to three unit pairs (a
multi-unit register map + per-unit ST programs) is Phase-2 work, alongside the
whole-plant visualization — see ROADMAP.md.

As in the single-unit bridge, the 3D/HMI ground truth is read directly from
``bridge.plant`` (never from these Modbus values); the operator-facing values
come back off each unit's (possibly compromised) SIS via ``hmi_pv`` /
``hmi_trip_status``.
"""

from __future__ import annotations

from dataclasses import dataclass

from bridge.plc_io import FakeBpcs, FakeSis
from common import registers as R
from physics.plant import PlantModel


@dataclass
class UnitSample:
    """One unit's record for a cycle — ground truth vs. what the operator sees."""
    unit_id: str
    tag: str
    pv_units: str
    true_pv: float          # true process variable (ground truth / 3D)
    feed: float             # effective BPCS drive applied to this unit
    trip_cmd: bool          # this unit's SIS trip asserted this cycle
    hmi_pv: float           # PV as shown on this unit's operator HMI (SIS-reported)
    hmi_trip_status: bool   # operator indicator: True = tripped, False = armed
    setpoint: float         # this unit's BPCS control setpoint (the attack surface)
    override: bool          # this unit's SIS_OVERRIDE coil
    failed: bool            # true once this unit passed its failure threshold

    @property
    def divergence(self) -> float:
        """|true PV − HMI PV| — this unit's spoof gap."""
        return abs(self.true_pv - self.hmi_pv)


@dataclass
class PlantSample:
    """One cycle across the whole plant."""
    time: float
    units: dict            # unit_id -> UnitSample
    q_coupling: float      # tank→reactor coupling applied this cycle
    p_coupling: float      # reactor→vessel coupling applied this cycle

    @property
    def any_failed(self) -> bool:
        return any(u.failed for u in self.units.values())


class PlantBridge:
    """Couples a ``PlantModel`` to one (BPCS, SIS) pair per unit."""

    def __init__(self, plant: PlantModel,
                 controllers: dict, dt: float = 0.1) -> None:
        # controllers: unit_id -> (bpcs: PlcClient, sis: PlcClient)
        self.plant = plant
        self.controllers = controllers
        self.dt = dt

    def prime_setpoints(self) -> None:
        """Seed every unit's BPCS setpoint with its operator-safe value.

        Called once at startup/reset (never per cycle), so a malicious setpoint
        later written over Modbus persists until reset — the same contract as
        the single-unit bridge, applied per unit.
        """
        for uid, m in self.plant.units:
            bpcs, _ = self.controllers[uid]
            bpcs.write_holding(R.BpcsHolding.SETPOINT, m.scenario.setpoint_reg)

    def step(self) -> PlantSample:
        commands = {}
        # 1-3. read each unit's control + safety decision off its own PLCs.
        for uid, m in self.plant.units:
            bpcs, sis = self.controllers[uid]
            pv_reg = m.scenario.pv_to_reg(m.pv)
            bpcs.write_holding(R.BpcsHolding.PV, pv_reg)
            sis.write_holding(R.SisHolding.PV, pv_reg)
            bpcs.scan()
            sis.scan()
            feed = R.reg_to_valve(bpcs.read_holding(R.BpcsHolding.FEED_CMD))
            trip = sis.read_coil(R.SisCoil.TRIP_CMD)
            commands[uid] = (feed, trip)

        # 4. step the whole plant once (couplings applied inside).
        self.plant.step(self.dt, commands)

        # 5. assemble the per-unit sample (ground truth + operator view).
        units = {}
        for uid, m in self.plant.units:
            bpcs, sis = self.controllers[uid]
            sc = m.scenario
            units[uid] = UnitSample(
                unit_id=uid,
                tag=sc.unit_tag,
                pv_units=sc.pv_units,
                true_pv=m.pv,
                feed=m.applied_feed,
                trip_cmd=commands[uid][1],
                hmi_pv=self.hmi_pv(uid),
                hmi_trip_status=self.hmi_trip_status(uid),
                setpoint=sc.reg_to_pv(bpcs.read_holding(R.BpcsHolding.SETPOINT)),
                override=sis.read_coil(R.SisCoil.OVERRIDE),
                failed=m.failed,
            )
        return PlantSample(time=self.plant.time, units=units,
                           q_coupling=self.plant.q_coupling,
                           p_coupling=self.plant.p_coupling)

    # --- attack surface (external, over Modbus — per unit) ------------------
    def attack(self, unit_id: str, bypass: bool = True) -> None:
        """Inject the evolved attack on ONE unit, leaving the others healthy.

        Flips that unit's BPCS to MANUAL and drives the actuator to its
        dangerous extreme (``attack_manual_out_reg`` — valve wide open / coolant
        shut), which bypasses the control law and the setpoint clamp. When
        ``bypass`` (the full attack) it also sets SIS_OVERRIDE to suppress the
        trip and freeze the HMI; with ``bypass=False`` the SIS is left armed —
        the defence-in-depth contrast, where a healthy SIS trips and holds the
        targeted unit safe despite the manual drive.
        """
        m = self.plant.unit(unit_id)
        bpcs, sis = self.controllers[unit_id]
        bpcs.write_coil(R.BpcsCoil.MAN_MODE, True)
        bpcs.write_holding(R.BpcsHolding.MAN_OUT, m.scenario.attack_manual_out_reg)
        if bypass:
            sis.write_coil(R.SisCoil.OVERRIDE, True)

    def stand_down(self, unit_id: str) -> None:
        """Undo :meth:`attack` on one unit: back to AUTO at the safe setpoint,
        clear the bypass. Does NOT clear a latched SIS trip (use the SIS reset)."""
        m = self.plant.unit(unit_id)
        bpcs, sis = self.controllers[unit_id]
        bpcs.write_coil(R.BpcsCoil.MAN_MODE, False)
        bpcs.write_holding(R.BpcsHolding.SETPOINT, m.scenario.setpoint_reg)
        sis.write_coil(R.SisCoil.OVERRIDE, False)

    # --- operator-facing (HMI) reads, off each unit's SIS -------------------
    def hmi_pv(self, unit_id: str) -> float:
        _, sis = self.controllers[unit_id]
        m = self.plant.unit(unit_id)
        return m.scenario.reg_to_pv(sis.read_holding(R.SisHolding.PV_HMI))

    def hmi_trip_status(self, unit_id: str) -> bool:
        _, sis = self.controllers[unit_id]
        return sis.read_coil(R.SisCoil.TRIP_CMD) or sis.read_coil(R.SisCoil.TRIP_STATUS)

    def close(self) -> None:
        for bpcs, sis in self.controllers.values():
            bpcs.close()
            sis.close()


def make_fake_plant_bridge(dt: float = 0.1) -> PlantBridge:
    """A fully wired connected plant on fake PLCs (no Docker/OpenPLC needed)."""
    plant = PlantModel()
    controllers = {}
    for uid, m in plant.units:
        sc = m.scenario
        controllers[uid] = (FakeBpcs(sc), FakeSis(sc.trip_reg))
    b = PlantBridge(plant, controllers, dt=dt)
    b.prime_setpoints()
    return b
