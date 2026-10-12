"""Authoritative Modbus register map (shared across all scenarios).

Single source of truth. The Modbus bridge, both OpenPLC programs, the HMI, and
the attack script all import addresses, scaling, and ranges from here — never
hard-code them per file. Mirrors the "Register map" tables in ARCHITECTURE.md;
if one changes, change both.

The map is **structurally identical for every scenario** — same instances,
tables, addresses, and directions. Only the *interpretation* of the process
variable (PV) register changes: tank level (mm), vessel pressure (cbar), or
reactor temperature (ddeg C). That per-scenario interpretation — the integer
scaling and the high-high trip threshold — lives in ``common/scenarios.py``, so
PV values are converted with ``scenario.pv_to_reg`` / ``scenario.reg_to_pv``
rather than a fixed level conversion here. The valve/actuator command is a
scenario-independent per-mille fraction and is scaled here.

Topology: two OpenPLC instances, each a separate Modbus slave with its OWN
address space. A register is addressed by (instance, table, address).

OpenPLC located-variable → Modbus mapping (verified against the running v3
runtime; see ARCHITECTURE.md "OpenPLC glue" note):

  * `%QXn.m` → coil `n*8+m`                 (program output / master R-W)
  * `%QWk`   → holding register `k`         (program OUTPUT image)
  * `%MWk`   → holding register `1024 + k`  (shared MEMORY word)

The direction matters. A `%QW` output is rewritten from the PLC program's image
every scan, so a Modbus master write to it is clobbered and never reaches the
program — it is only good for program→master values (actuator command, HMI
mirror). The SENSOR injection (master→program) therefore uses a `%MW` MEMORY
word (`MW_HOLDING_BASE + k`), which the master and the program share in place
with no scan clobber. Coils are shared too, so the override coil works
master→program. This replaces the earlier (incorrect) `%QW0`-sensor mapping.

The BPCS `SETPOINT` register (`%MW1` = holding 1025) is master→program for the
same reason: the operator (and, in the attack, the external attacker) writes the
control setpoint, and the BPCS program only reads it. The bridge seeds it with
the safe setpoint at startup/reset and never rewrites it afterwards, so a
malicious setpoint written over Modbus persists until the next reset.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum

from common import deploy_config as _D

# --- Scaling (PV scaling is per-scenario; see common/scenarios.py) ---------
VALVE_PER_MILLE = 1000  # valve register = round(fraction * 1000), unit = per-mille
VALVE_REG_MAX = 1000    # fully open
REG_U16_MAX = 65535     # Modbus 16-bit register ceiling

# OpenPLC maps %MWk to holding register MW_HOLDING_BASE + k (shared memory word).
MW_HOLDING_BASE = 1024


# --- Instances and their default Modbus endpoints --------------------------
class Instance(str, Enum):
    BPCS = "bpcs"  # Control PLC
    SIS = "sis"    # Safety PLC


# Host endpoints. Default to the single-host container ports (see
# plc/docker-compose.yml); overridable per PETROTWIN_BPCS_HOST/PETROTWIN_SIS_HOST
# (+ _MODBUS_PORT) for the Phase-2.5 distributed layout — see
# common/deploy_config.py / deploy/README.md. Never hard-code a host elsewhere.
ENDPOINTS = {
    Instance.BPCS: _D.BPCS_MODBUS,
    Instance.SIS: _D.SIS_MODBUS,
}


# --- BPCS (Control PLC) registers ------------------------------------------
class BpcsHolding(IntEnum):
    """BPCS holding registers (master read/write)."""
    PV = MW_HOLDING_BASE        # %MW0 (holding 1024) physics -> PLC sensor; program reads
    SETPOINT = MW_HOLDING_BASE + 1  # %MW1 (holding 1025) operator/ATTACK -> PLC; program reads
    FEED_CMD = 1                # %QW1 PLC -> physics (per-mille); program writes
    # %MW2 (holding 1026) manual actuator command, per-mille. Master<->PLC: in
    # AUTO the program TRACKS the computed output here (bumpless transfer); in
    # MANUAL the program reads the master's value as the actuator position. The
    # evolved attack drives this directly, bypassing the setpoint clamp.
    MAN_OUT = MW_HOLDING_BASE + 2


class BpcsCoil(IntEnum):
    """BPCS coils (master read/write). The BPCS had none until Auto/Manual mode
    and the operator export valve were added."""
    MAN_MODE = 0      # %QX0.0  operator/attack -> PLC; 0 = AUTO, 1 = MANUAL (program reads)
    PV_HI_ALARM = 1   # %QX0.1  PLC -> HMI;  pv >= SP_HI
    PV_LO_ALARM = 2   # %QX0.2  PLC -> HMI;  pv <= SP_LO
    SP_CLAMPED = 3    # %QX0.3  PLC -> HMI;  operator setpoint was out of range
    OPV_CMD = 4       # %QX0.4  operator -> PLC; export-valve open request (program reads)
    OPV_OUT = 5       # %QX0.5  PLC -> physics/HMI; export-valve state (= OPV_CMD)


# --- SIS (Safety PLC) registers --------------------------------------------
class SisCoil(IntEnum):
    """SIS coils (master read/write)."""
    TRIP_CMD = 0       # %QX0.0  PLC -> physics; 1 = force protective action
    TRIP_STATUS = 1    # %QX0.1  PLC -> HMI;     1 = tripped, 0 = armed-normal
    OVERRIDE = 2       # %QX0.2  attack -> PLC;  maintenance BYPASS/MOS (program reads)
    RESET = 3          # %QX0.3  operator -> PLC; manual reset pushbutton (program reads)
    PREALARM_H = 4     # %QX0.4  PLC -> HMI;  pv >= PRE_ALARM_H (below the HH trip)
    PV_BAD = 5         # %QX0.5  PLC -> HMI;  transmitter out of range (fail-safe trip)
    BLK_OPEN_CMD = 6   # %QX0.6  operator -> PLC; block/fill-valve open request (program reads)
    BLK_OUT = 7        # %QX0.7  PLC -> physics/HMI; block valve = cmd AND NOT trip
    BYPASS_ACTIVE = 8  # %QX1.0  PLC -> HMI;  MOS annunciation (= OVERRIDE); byte 1


class SisHolding(IntEnum):
    """SIS holding registers (master read/write)."""
    PV = MW_HOLDING_BASE  # %MW0 (holding 1024) physics -> PLC sensor; program reads
    PV_HMI = 1            # %QW1 PLC -> HMI (scaled PV); frozen under OVERRIDE


@dataclass(frozen=True)
class RegInfo:
    instance: Instance
    table: str      # "coil" | "holding"
    st_var: str     # OpenPLC located variable, e.g. "%QW0" / "%QX0.2" / "%QX1.0"
    direction: str  # physics->PLC | PLC->physics | PLC->HMI | attack->PLC | operator->PLC
    units: str      # "pv (scaled)" | "per-mille" | "bool"


def _qx(addr: int) -> str:
    """OpenPLC coil located variable for a Modbus coil address: coil n*8+m is
    %QXn.m, so byte = addr // 8 and bit = addr % 8 (coils roll into byte 1 at
    address 8 — e.g. BYPASS_ACTIVE at 8 -> %QX1.0)."""
    return f"%QX{addr // 8}.{addr % 8}"


# Flat metadata view keyed by (instance, table, address).
REGISTERS = {
    (Instance.BPCS, "holding", BpcsHolding.PV):
        RegInfo(Instance.BPCS, "holding", "%MW0", "physics->PLC", "pv (scaled)"),
    (Instance.BPCS, "holding", BpcsHolding.SETPOINT):
        RegInfo(Instance.BPCS, "holding", "%MW1", "attack->PLC", "pv (scaled)"),
    (Instance.BPCS, "holding", BpcsHolding.MAN_OUT):
        RegInfo(Instance.BPCS, "holding", "%MW2", "attack->PLC", "per-mille"),
    (Instance.BPCS, "holding", BpcsHolding.FEED_CMD):
        RegInfo(Instance.BPCS, "holding", "%QW1", "PLC->physics", "per-mille"),
    (Instance.BPCS, "coil", BpcsCoil.MAN_MODE):
        RegInfo(Instance.BPCS, "coil", _qx(BpcsCoil.MAN_MODE), "attack->PLC", "bool"),
    (Instance.BPCS, "coil", BpcsCoil.PV_HI_ALARM):
        RegInfo(Instance.BPCS, "coil", _qx(BpcsCoil.PV_HI_ALARM), "PLC->HMI", "bool"),
    (Instance.BPCS, "coil", BpcsCoil.PV_LO_ALARM):
        RegInfo(Instance.BPCS, "coil", _qx(BpcsCoil.PV_LO_ALARM), "PLC->HMI", "bool"),
    (Instance.BPCS, "coil", BpcsCoil.SP_CLAMPED):
        RegInfo(Instance.BPCS, "coil", _qx(BpcsCoil.SP_CLAMPED), "PLC->HMI", "bool"),
    (Instance.BPCS, "coil", BpcsCoil.OPV_CMD):
        RegInfo(Instance.BPCS, "coil", _qx(BpcsCoil.OPV_CMD), "operator->PLC", "bool"),
    (Instance.BPCS, "coil", BpcsCoil.OPV_OUT):
        RegInfo(Instance.BPCS, "coil", _qx(BpcsCoil.OPV_OUT), "PLC->physics", "bool"),
    (Instance.SIS, "coil", SisCoil.TRIP_CMD):
        RegInfo(Instance.SIS, "coil", _qx(SisCoil.TRIP_CMD), "PLC->physics", "bool"),
    (Instance.SIS, "coil", SisCoil.TRIP_STATUS):
        RegInfo(Instance.SIS, "coil", _qx(SisCoil.TRIP_STATUS), "PLC->HMI", "bool"),
    (Instance.SIS, "coil", SisCoil.OVERRIDE):
        RegInfo(Instance.SIS, "coil", _qx(SisCoil.OVERRIDE), "attack->PLC", "bool"),
    (Instance.SIS, "coil", SisCoil.RESET):
        RegInfo(Instance.SIS, "coil", _qx(SisCoil.RESET), "operator->PLC", "bool"),
    (Instance.SIS, "coil", SisCoil.PREALARM_H):
        RegInfo(Instance.SIS, "coil", _qx(SisCoil.PREALARM_H), "PLC->HMI", "bool"),
    (Instance.SIS, "coil", SisCoil.PV_BAD):
        RegInfo(Instance.SIS, "coil", _qx(SisCoil.PV_BAD), "PLC->HMI", "bool"),
    (Instance.SIS, "coil", SisCoil.BLK_OPEN_CMD):
        RegInfo(Instance.SIS, "coil", _qx(SisCoil.BLK_OPEN_CMD), "operator->PLC", "bool"),
    (Instance.SIS, "coil", SisCoil.BLK_OUT):
        RegInfo(Instance.SIS, "coil", _qx(SisCoil.BLK_OUT), "PLC->physics", "bool"),
    (Instance.SIS, "coil", SisCoil.BYPASS_ACTIVE):
        RegInfo(Instance.SIS, "coil", _qx(SisCoil.BYPASS_ACTIVE), "PLC->HMI", "bool"),
    (Instance.SIS, "holding", SisHolding.PV):
        RegInfo(Instance.SIS, "holding", "%MW0", "physics->PLC", "pv (scaled)"),
    (Instance.SIS, "holding", SisHolding.PV_HMI):
        RegInfo(Instance.SIS, "holding", "%QW1", "PLC->HMI", "pv (scaled)"),
}


# --- Valve scaling helpers (scenario-independent) --------------------------
def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def valve_to_reg(fraction: float) -> int:
    """Valve fraction [0,1] -> valve register (per-mille), clamped."""
    return int(round(_clamp(fraction, 0.0, 1.0) * VALVE_PER_MILLE))


def reg_to_valve(reg: int) -> float:
    """Valve register (per-mille) -> fraction [0,1], clamped."""
    return _clamp(reg / VALVE_PER_MILLE, 0.0, 1.0)


# --- Multi-unit addressing (connected-plant LIVE path) ---------------------
# The register map above is the single-unit block: exactly one PV/BPCS/SIS set,
# addressed from zero. The connected whole-plant view runs THREE units (tank,
# reactor, vessel) on just TWO OpenPLC slaves — one BPCS carrying control for
# all three, one SIS carrying safety for all three. Each unit gets its own block
# within a slave by adding a per-unit OFFSET to the single-unit addresses, so
# the three units never collide on the shared Modbus image.
#
# The fake-PLC path does NOT use these: there each unit owns a separate in-memory
# FakeBpcs/FakeSis, so the identical base addresses never collide (see
# bridge/plant_bridge.py). Offsets are only for the real ModbusPlc path, applied
# by bridge.plc_io.ModbusUnitView and matched byte-for-byte by the multi-unit ST
# programs generated in plc/load_scenario.py.
#
# Strides are chosen to keep every unit byte-aligned and each unit's writable
# input coils in its block's byte 0, and to fit OpenPLC v3's default image:
#   coils  tank %QX0.* / reactor %QX2.* / vessel %QX4.*  (SIS spills to the next
#          byte: %QX0-1 / %QX2-3 / %QX4-5)               -> max coil 40
#   %MW    tank %MW0-2 / reactor %MW16-18 / vessel %MW32-34 (holding 1024..1058)
#   %QW    tank %QW1   / reactor %QW9     / vessel %QW17
UNIT_INDEX = {"tank": 0, "reactor": 1, "vessel": 2}  # must match physics.plant.FLOW
COIL_STRIDE = 16   # coils per unit (2 bytes: SIS uses 9 coils, rolls into byte 1)
MW_STRIDE = 16     # %MW words per unit (BPCS uses 3: PV/SETPOINT/MAN_OUT)
QW_STRIDE = 8      # %QW words per unit (one each: BPCS FEED_CMD / SIS PV_HMI)


def unit_addr(table: str, base_addr: int, unit_index: int) -> int:
    """Project a single-unit Modbus address into unit ``unit_index``'s block.

    ``table`` is "coil" or "holding"; ``base_addr`` is the single-unit address
    (an enum value from BpcsCoil/SisCoil/BpcsHolding/SisHolding). Unit 0 (tank)
    maps to the base address unchanged, so the single-unit map stays canonical.
    """
    if unit_index == 0:
        return int(base_addr)
    if table == "coil":
        return int(base_addr) + unit_index * COIL_STRIDE
    if int(base_addr) >= MW_HOLDING_BASE:        # %MW memory-word band
        return int(base_addr) + unit_index * MW_STRIDE
    return int(base_addr) + unit_index * QW_STRIDE  # %QW output band
