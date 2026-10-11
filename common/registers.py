"""Authoritative Modbus register map (Phase 0, tank-overfill scenario).

Single source of truth. The Modbus bridge, both OpenPLC programs, the HMI, and
the attack script all import addresses, scaling, and ranges from here — never
hard-code them per file. Mirrors the "Register map — Phase 0" tables in
ARCHITECTURE.md; if one changes, change both.

Topology: two OpenPLC instances, each a separate Modbus slave with its OWN
address space starting at 0. A register is addressed by (instance, table,
address). OpenPLC maps located variables to Modbus as `%QWk` -> holding
register k and `%QXn.m` -> coil n*8+m; a master may write coils and holding
registers and read every table, so sensors are injected into `%QW` holding
registers the program treats as inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum

# --- Scaling ---------------------------------------------------------------
LEVEL_MM_PER_M = 1000   # level register = round(level_m * 1000), unit = mm
VALVE_PER_MILLE = 1000  # valve register = round(fraction * 1000), unit = per-mille
LEVEL_REG_MAX = 10000   # 10.0 m full scale
VALVE_REG_MAX = 1000    # fully open
REG_U16_MAX = 65535     # Modbus 16-bit register ceiling


# --- Instances and their default Modbus endpoints --------------------------
class Instance(str, Enum):
    BPCS = "bpcs"  # Control PLC
    SIS = "sis"    # Safety PLC


# Default host endpoints (host-mapped container ports; see plc/docker-compose.yml).
ENDPOINTS = {
    Instance.BPCS: ("127.0.0.1", 5020),
    Instance.SIS: ("127.0.0.1", 5021),
}


# --- BPCS (Control PLC) registers ------------------------------------------
class BpcsHolding(IntEnum):
    """BPCS holding registers (master read/write)."""
    LEVEL_PV = 0  # %QW0  physics -> PLC (mm); program reads
    FEED_CMD = 1  # %QW1  PLC -> physics (per-mille); program writes


# --- SIS (Safety PLC) registers --------------------------------------------
class SisCoil(IntEnum):
    """SIS coils (master read/write)."""
    TRIP_CMD = 0     # %QX0.0  PLC -> physics; 1 = force feed shut
    TRIP_STATUS = 1  # %QX0.1  PLC -> HMI;     1 = tripped, 0 = armed-normal
    OVERRIDE = 2     # %QX0.2  attack -> PLC;  1 = force/bypass (program reads)


class SisHolding(IntEnum):
    """SIS holding registers (master read/write)."""
    LEVEL_PV = 0   # %QW0  physics -> PLC (mm); program reads
    LEVEL_HMI = 1  # %QW1  PLC -> HMI (mm); frozen under OVERRIDE


@dataclass(frozen=True)
class RegInfo:
    instance: Instance
    table: str      # "coil" | "holding"
    st_var: str     # OpenPLC located variable, e.g. "%QW0" / "%QX0.2"
    direction: str  # "physics->PLC" | "PLC->physics" | "PLC->HMI" | "attack->PLC"
    units: str      # "mm" | "per-mille" | "bool"


# Flat metadata view keyed by (instance, table, address).
REGISTERS = {
    (Instance.BPCS, "holding", BpcsHolding.LEVEL_PV):
        RegInfo(Instance.BPCS, "holding", "%QW0", "physics->PLC", "mm"),
    (Instance.BPCS, "holding", BpcsHolding.FEED_CMD):
        RegInfo(Instance.BPCS, "holding", "%QW1", "PLC->physics", "per-mille"),
    (Instance.SIS, "coil", SisCoil.TRIP_CMD):
        RegInfo(Instance.SIS, "coil", "%QX0.0", "PLC->physics", "bool"),
    (Instance.SIS, "coil", SisCoil.TRIP_STATUS):
        RegInfo(Instance.SIS, "coil", "%QX0.1", "PLC->HMI", "bool"),
    (Instance.SIS, "coil", SisCoil.OVERRIDE):
        RegInfo(Instance.SIS, "coil", "%QX0.2", "attack->PLC", "bool"),
    (Instance.SIS, "holding", SisHolding.LEVEL_PV):
        RegInfo(Instance.SIS, "holding", "%QW0", "physics->PLC", "mm"),
    (Instance.SIS, "holding", SisHolding.LEVEL_HMI):
        RegInfo(Instance.SIS, "holding", "%QW1", "PLC->HMI", "mm"),
}


# --- Scaling helpers -------------------------------------------------------
def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def level_to_reg(level_m: float) -> int:
    """Metres -> level register (mm), clamped to the register range."""
    return int(round(_clamp(level_m * LEVEL_MM_PER_M, 0, LEVEL_REG_MAX)))


def reg_to_level(reg: int) -> float:
    """Level register (mm) -> metres."""
    return reg / LEVEL_MM_PER_M


def valve_to_reg(fraction: float) -> int:
    """Valve fraction [0,1] -> valve register (per-mille), clamped."""
    return int(round(_clamp(fraction, 0.0, 1.0) * VALVE_PER_MILLE))


def reg_to_valve(reg: int) -> float:
    """Valve register (per-mille) -> fraction [0,1], clamped."""
    return _clamp(reg / VALVE_PER_MILLE, 0.0, 1.0)
