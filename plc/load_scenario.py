"""Load the PLC programs for a chosen scenario into the two OpenPLC instances.

Loads the BPCS control program — with the scenario's proportional-control
constants substituted — into the Control PLC, and the SIS program — with the
scenario's high-high trip threshold substituted — into the Safety PLC, then
compiles and starts both. This keeps the live PLC programs in lock-step with the
single source of truth (``common/scenarios.py``): the control gains
(``ctrl_bias_pm`` / ``ctrl_kp_num`` / ``ctrl_kp_den``) and the SIS trip
(``trip_reg``) are never hand-copied into a per-scenario file.

Usage (run as a module from the repo root so `common` is importable):
    python3 -m plc.load_scenario <scenario> [bpcs_url] [sis_url]
    python3 -m plc.load_scenario tank
    python3 -m plc.load_scenario reactor http://127.0.0.1:8081 http://127.0.0.1:8082
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from common import deploy_config as D
from common import registers as R
from common import scenarios
from plc.load_program import load, load_text

_HERE = Path(__file__).resolve().parent
BPCS_ST = _HERE / "bpcs" / "bpcs.st"
SIS_TEMPLATE = _HERE / "sis" / "sis.st"

# Default to the single-host web UI ports; PETROTWIN_BPCS_HOST/PETROTWIN_SIS_HOST
# (see common/deploy_config.py) point these at PC2/PC3 for the distributed
# layout. An explicit bpcs_url/sis_url argument (CLI or load_plant/load_scenario)
# always wins over both.
BPCS_URL = D.BPCS_WEB
SIS_URL = D.SIS_WEB

# Matches the `TRIP_HH : INT := <n>;` line the SIS template promises to keep.
_TRIP_RE = re.compile(r"(TRIP_HH\s*:\s*INT\s*:=\s*)\d+(\s*;)")

# The other per-scenario SIS constants, in the form `NAME : INT := <n>;`
# (PV_MIN is fixed at 0 and intentionally NOT substituted).
_SIS_INT_CONSTS = (("PRE_ALARM_H", "pre_alarm_h_reg"),
                   ("RESET_DB", "reset_db_reg"),
                   ("PV_MAX", "reg_full_scale"))

# The BPCS control constants the template promises to keep, in the form
# `NAME : DINT := <n>;` (KP_NUM is signed).
_BPCS_CONSTS = (("BIAS_PM", "ctrl_bias_pm"),
                ("KP_NUM", "ctrl_kp_num"),
                ("KP_DEN", "ctrl_kp_den"))

# The BPCS operator-setpoint clamp bounds, in the form `NAME : INT := <n>;`
# (register units; RATE_LIM is intentionally NOT substituted — it is fixed).
_BPCS_INT_CONSTS = (("SP_LO", "sp_lo_reg"),
                    ("SP_HI", "sp_hi_reg"))


def sis_program_for(scenario_id: str) -> str:
    """The SIS ST program for a scenario: the template with TRIP_HH substituted."""
    sc = scenarios.get(scenario_id)
    new_text = SIS_TEMPLATE.read_text()
    new_text, n = _TRIP_RE.subn(rf"\g<1>{sc.trip_reg}\g<2>", new_text, count=1)
    if n != 1:
        raise SystemExit(
            "could not find the 'TRIP_HH : INT := <n>;' line in "
            f"{SIS_TEMPLATE} to substitute the scenario trip threshold")
    for const, attr in _SIS_INT_CONSTS:
        pat = re.compile(rf"({const}\s*:\s*INT\s*:=\s*)-?\d+(\s*;)")
        new_text, n = pat.subn(rf"\g<1>{getattr(sc, attr)}\g<2>", new_text, count=1)
        if n != 1:
            raise SystemExit(
                f"could not find the '{const} : INT := <n>;' line in "
                f"{SIS_TEMPLATE} to substitute the scenario SIS constant")
    return new_text


def bpcs_program_for(scenario_id: str) -> str:
    """The BPCS ST program for a scenario: the template with the proportional
    control constants (bias / gain) substituted from common/scenarios.py."""
    sc = scenarios.get(scenario_id)
    text = BPCS_ST.read_text()
    for const, attr in _BPCS_CONSTS:
        pat = re.compile(rf"({const}\s*:\s*DINT\s*:=\s*)-?\d+(\s*;)")
        text, n = pat.subn(rf"\g<1>{getattr(sc, attr)}\g<2>", text, count=1)
        if n != 1:
            raise SystemExit(
                f"could not find the '{const} : DINT := <n>;' line in "
                f"{BPCS_ST} to substitute the scenario control constant")
    for const, attr in _BPCS_INT_CONSTS:
        pat = re.compile(rf"({const}\s*:\s*INT\s*:=\s*)-?\d+(\s*;)")
        text, n = pat.subn(rf"\g<1>{getattr(sc, attr)}\g<2>", text, count=1)
        if n != 1:
            raise SystemExit(
                f"could not find the '{const} : INT := <n>;' line in "
                f"{BPCS_ST} to substitute the scenario setpoint-clamp bound")
    return text


# --- Multi-unit (connected-plant) program generation -----------------------
# The connected whole-plant view runs all three units on just two OpenPLC
# slaves: one BPCS program and one SIS program, each with THREE located-variable
# instances (one per unit) at the per-unit offset addresses from
# common/registers.py. Each instance is the canonical single-unit program with
# that unit's constants substituted and its located addresses shifted by the
# unit's block offset — so the generated program stays byte-for-byte consistent
# with registers.unit_addr and the FakeBpcs/FakeSis lock-step.

# Matches the single `PROGRAM prog0 ... END_PROGRAM` POU in each template.
_PROG_RE = re.compile(r"PROGRAM\s+prog0\b.*?END_PROGRAM", re.DOTALL)


def _shift_located_addrs(prog_text: str, unit_index: int) -> str:
    """Offset every %MW/%QW/%QX located address in a POU into a unit's block,
    using the same strides as registers.unit_addr (unit 0 is unchanged)."""
    if unit_index == 0:
        return prog_text

    def mw(m):
        return f"%MW{int(m.group(1)) + unit_index * R.MW_STRIDE}"

    def qw(m):
        return f"%QW{int(m.group(1)) + unit_index * R.QW_STRIDE}"

    def qx(m):
        coil = int(m.group(1)) * 8 + int(m.group(2)) + unit_index * R.COIL_STRIDE
        return f"%QX{coil // 8}.{coil % 8}"

    t = re.sub(r"%MW(\d+)", mw, prog_text)
    t = re.sub(r"%QW(\d+)", qw, t)
    t = re.sub(r"%QX(\d+)\.(\d+)", qx, t)
    return t


def _plant_program(single_unit_for, kind: str) -> str:
    """Build a 3-instance plant program (one POU per unit) from the single-unit
    per-scenario program produced by ``single_unit_for`` (bpcs_/sis_program_for)."""
    blocks = []
    for uid in scenarios.ids():
        prog = _PROG_RE.search(single_unit_for(uid))
        if not prog:
            raise SystemExit(f"could not find 'PROGRAM prog0 ... END_PROGRAM' for {uid}")
        text = _shift_located_addrs(prog.group(0), R.UNIT_INDEX[uid])
        text = re.sub(r"\bprog0\b", f"prog_{uid}", text)
        blocks.append(f"(* ---- unit: {uid} (block offset {R.UNIT_INDEX[uid]}) ---- *)\n{text}")
    insts = "\n".join(f"    PROGRAM inst_{uid} WITH task0 : prog_{uid};"
                      for uid in scenarios.ids())
    config = ("CONFIGURATION Config0\n"
              "  RESOURCE Res0 ON PLC\n"
              "    TASK task0(INTERVAL := T#100ms, PRIORITY := 0);\n"
              f"{insts}\n"
              "  END_RESOURCE\n"
              "END_CONFIGURATION\n")
    header = (f"(* PetroTwin {kind} plant program — GENERATED by plc/load_scenario.py.\n"
              f" * Three unit instances (tank/reactor/vessel) on one OpenPLC slave, each at\n"
              f" * its per-unit offset block (see common/registers.py unit_addr). Do not edit\n"
              f" * by hand — regenerate from the single-unit templates + common/scenarios.py. *)\n")
    return header + "\n" + "\n\n".join(blocks) + "\n\n" + config


def bpcs_plant_program() -> str:
    """The BPCS control program for all three units (one POU per unit)."""
    return _plant_program(bpcs_program_for, "BPCS")


def sis_plant_program() -> str:
    """The SIS safety program for all three units (one POU per unit)."""
    return _plant_program(sis_program_for, "SIS")


def load_plant(bpcs_url: str = BPCS_URL, sis_url: str = SIS_URL) -> None:
    """Load the connected whole-plant programs into the two OpenPLC slaves: one
    BPCS carrying control for all three units, one SIS carrying safety."""
    print("Loading CONNECTED-PLANT programs (tank+reactor+vessel) into both PLCs:\n"
          f"  BPCS <- {len(scenarios.ids())} control instances\n"
          f"  SIS  <- {len(scenarios.ids())} safety instances\n")
    load_text(bpcs_url, bpcs_plant_program(), "BPCS plant control",
              source="bpcs.st x3 (tank/reactor/vessel)")
    load_text(sis_url, sis_plant_program(), "SIS plant safety",
              source="sis.st x3 (tank/reactor/vessel)")
    print("\nLoaded connected plant. Both PLCs compiled and started.")


def load_scenario(scenario_id: str, bpcs_url: str = BPCS_URL,
                  sis_url: str = SIS_URL) -> None:
    sc = scenarios.get(scenario_id)
    print(f"Loading PLC programs for scenario '{sc.id}' ({sc.title}) — "
          f"setpoint {sc.setpoint} {sc.pv_units} = {sc.setpoint_reg} (reg units), "
          f"SIS trip at {sc.trip_hh} {sc.pv_units} = {sc.trip_reg} (reg units)\n")
    load_text(bpcs_url, bpcs_program_for(sc.id), f"BPCS {sc.id} setpoint control",
              source=f"{BPCS_ST.name} [bias={sc.ctrl_bias_pm}, "
                     f"kp={sc.ctrl_kp_num}/{sc.ctrl_kp_den}]")
    load_text(sis_url, sis_program_for(sc.id), f"SIS {sc.id} high-high trip",
              source=f"{SIS_TEMPLATE.name} [trip_hh={sc.trip_reg}]")
    print(f"\nLoaded scenario '{sc.id}'. Both PLCs compiled and started.")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        print(f"scenarios: {scenarios.ids()}")
        raise SystemExit(2)
    if args[0] == "--plant":
        bpcs_url = args[1] if len(args) > 1 else BPCS_URL
        sis_url = args[2] if len(args) > 2 else SIS_URL
        load_plant(bpcs_url, sis_url)
        return
    sid = args[0]
    bpcs_url = args[1] if len(args) > 1 else BPCS_URL
    sis_url = args[2] if len(args) > 2 else SIS_URL
    load_scenario(sid, bpcs_url, sis_url)


if __name__ == "__main__":
    main()
