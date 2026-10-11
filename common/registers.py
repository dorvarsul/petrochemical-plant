"""Authoritative Modbus register map (Phase 0, tank-overfill scenario).

Single source of truth. The Modbus bridge, both OpenPLC programs, the HMI, and
the attack script all import addresses, scaling, and ranges from here — never
hard-code them per file. This module mirrors the "Register map — Phase 0" table
in ARCHITECTURE.md; if one changes, change both.

Modbus model: each OpenPLC instance is a slave; the bridge is the master. Coils
and holding registers are separate address spaces (so coil 10 and holding
register 10 are distinct). Owner blocks: BPCS = 0-9, SIS = 10-19.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

# --- Scaling ---------------------------------------------------------------
LEVEL_MM_PER_M = 1000   # level register = round(level_m * 1000), unit = mm
VALVE_PER_MILLE = 1000  # valve register = round(fraction * 1000), unit = per-mille
LEVEL_REG_MAX = 10000   # 10.0 m full scale
VALVE_REG_MAX = 1000    # fully open
REG_U16_MAX = 65535     # Modbus 16-bit register ceiling


class Coil(IntEnum):
    """Discrete (1-bit) registers — master read/write."""
    SIS_TRIP_CMD = 10     # Safety/SIS -> physics: 1 = trip (force feed shut)
    SIS_TRIP_STATUS = 11  # Safety/SIS -> HMI:     1 = tripped, 0 = armed-normal
    SIS_OVERRIDE = 12     # attack    -> SIS:      1 = force/bypass (suppress trip)


class Holding(IntEnum):
    """16-bit registers — master read/write."""
    BPCS_LEVEL_PV = 0   # physics -> BPCS: true level (mm)
    BPCS_FEED_CMD = 1   # BPCS -> physics: fill-valve position (per-mille)
    SIS_LEVEL_PV = 10   # physics -> SIS:  true level (mm)
    SIS_LEVEL_HMI = 11  # SIS -> HMI:      displayed level (mm); attack freezes


@dataclass(frozen=True)
class RegInfo:
    owner: str        # "BPCS" | "SIS"
    direction: str    # e.g. "physics->PLC", "PLC->HMI", "PLC->physics", "attack->PLC"
    units: str        # "mm", "per-mille", "bool"


COIL_INFO = {
    Coil.SIS_TRIP_CMD:    RegInfo("SIS", "PLC->physics", "bool"),
    Coil.SIS_TRIP_STATUS: RegInfo("SIS", "PLC->HMI", "bool"),
    Coil.SIS_OVERRIDE:    RegInfo("SIS", "attack->PLC", "bool"),
}

HOLDING_INFO = {
    Holding.BPCS_LEVEL_PV: RegInfo("BPCS", "physics->PLC", "mm"),
    Holding.BPCS_FEED_CMD: RegInfo("BPCS", "PLC->physics", "per-mille"),
    Holding.SIS_LEVEL_PV:  RegInfo("SIS", "physics->PLC", "mm"),
    Holding.SIS_LEVEL_HMI: RegInfo("SIS", "PLC->HMI", "mm"),
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
