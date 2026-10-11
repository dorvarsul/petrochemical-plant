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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum

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


# Default host endpoints (host-mapped container ports; see plc/docker-compose.yml).
ENDPOINTS = {
    Instance.BPCS: ("127.0.0.1", 5020),
    Instance.SIS: ("127.0.0.1", 5021),
}


# --- BPCS (Control PLC) registers ------------------------------------------
class BpcsHolding(IntEnum):
    """BPCS holding registers (master read/write)."""
    PV = MW_HOLDING_BASE  # %MW0 (holding 1024) physics -> PLC sensor; program reads
    FEED_CMD = 1          # %QW1 PLC -> physics (per-mille); program writes


# --- SIS (Safety PLC) registers --------------------------------------------
class SisCoil(IntEnum):
    """SIS coils (master read/write)."""
    TRIP_CMD = 0     # %QX0.0  PLC -> physics; 1 = force protective action
    TRIP_STATUS = 1  # %QX0.1  PLC -> HMI;     1 = tripped, 0 = armed-normal
    OVERRIDE = 2     # %QX0.2  attack -> PLC;  1 = force/bypass (program reads)


class SisHolding(IntEnum):
    """SIS holding registers (master read/write)."""
    PV = MW_HOLDING_BASE  # %MW0 (holding 1024) physics -> PLC sensor; program reads
    PV_HMI = 1            # %QW1 PLC -> HMI (scaled PV); frozen under OVERRIDE


@dataclass(frozen=True)
class RegInfo:
    instance: Instance
    table: str      # "coil" | "holding"
    st_var: str     # OpenPLC located variable, e.g. "%QW0" / "%QX0.2"
    direction: str  # "physics->PLC" | "PLC->physics" | "PLC->HMI" | "attack->PLC"
    units: str      # "pv (scaled)" | "per-mille" | "bool"


# Flat metadata view keyed by (instance, table, address).
REGISTERS = {
    (Instance.BPCS, "holding", BpcsHolding.PV):
        RegInfo(Instance.BPCS, "holding", "%MW0", "physics->PLC", "pv (scaled)"),
    (Instance.BPCS, "holding", BpcsHolding.FEED_CMD):
        RegInfo(Instance.BPCS, "holding", "%QW1", "PLC->physics", "per-mille"),
    (Instance.SIS, "coil", SisCoil.TRIP_CMD):
        RegInfo(Instance.SIS, "coil", "%QX0.0", "PLC->physics", "bool"),
    (Instance.SIS, "coil", SisCoil.TRIP_STATUS):
        RegInfo(Instance.SIS, "coil", "%QX0.1", "PLC->HMI", "bool"),
    (Instance.SIS, "coil", SisCoil.OVERRIDE):
        RegInfo(Instance.SIS, "coil", "%QX0.2", "attack->PLC", "bool"),
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
