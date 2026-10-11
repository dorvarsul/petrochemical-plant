"""Load the PLC programs for a chosen scenario into the two OpenPLC instances.

Loads the shared BPCS program into the Control PLC and the SIS program — with
the scenario's high-high trip threshold substituted from common/scenarios.py —
into the Safety PLC, then compiles and starts both. This keeps the live SIS trip
threshold in lock-step with the single source of truth (``scenario.trip_reg``):
the constant is never hand-copied into a per-scenario file.

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

# Matches the `TRIP_HH : INT := <n>;` line the template promises to keep.
_TRIP_RE = re.compile(r"(TRIP_HH\s*:\s*INT\s*:=\s*)\d+(\s*;)")


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


def load_scenario(scenario_id: str, bpcs_url: str = BPCS_URL,
                  sis_url: str = SIS_URL) -> None:
    sc = scenarios.get(scenario_id)
    print(f"Loading PLC programs for scenario '{sc.id}' ({sc.title}) — "
          f"SIS trip at {sc.trip_hh} {sc.pv_units} = {sc.trip_reg} (reg units)\n")
    load(bpcs_url, str(BPCS_ST), "BPCS feed control")
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
