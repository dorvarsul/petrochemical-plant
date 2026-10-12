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

from common import scenarios
from plc.load_program import load, load_text

_HERE = Path(__file__).resolve().parent
BPCS_ST = _HERE / "bpcs" / "bpcs.st"
SIS_TEMPLATE = _HERE / "sis" / "sis.st"

BPCS_URL = "http://127.0.0.1:8081"
SIS_URL = "http://127.0.0.1:8082"

# Matches the `TRIP_HH : INT := <n>;` line the SIS template promises to keep.
_TRIP_RE = re.compile(r"(TRIP_HH\s*:\s*INT\s*:=\s*)\d+(\s*;)")

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
    template = SIS_TEMPLATE.read_text()
    new_text, n = _TRIP_RE.subn(rf"\g<1>{sc.trip_reg}\g<2>", template, count=1)
    if n != 1:
        raise SystemExit(
            "could not find the 'TRIP_HH : INT := <n>;' line in "
            f"{SIS_TEMPLATE} to substitute the scenario trip threshold")
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
    sid = args[0]
    bpcs_url = args[1] if len(args) > 1 else BPCS_URL
    sis_url = args[2] if len(args) > 2 else SIS_URL
    load_scenario(sid, bpcs_url, sis_url)


if __name__ == "__main__":
    main()
