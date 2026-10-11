"""Live end-to-end divergence demo over REAL Modbus + OpenPLC.

Same scenario story as run_fake_demo, but the Bridge drives the two real OpenPLC
v3 slaves via pymodbus. This exercises the actual time-sync surface (physics
clock vs. the PLC scan cycle over Modbus) — the #1 Phase-0 risk.

Prerequisites: both OpenPLC containers up with the BPCS program and the SIS
program FOR THE CHOSEN SCENARIO loaded and running (see plc/load_scenario.py).
Needs pymodbus (requirements.txt).

Run:  .venv/bin/python -m bridge.run_live_demo [tank|vessel|reactor]
      PETROTWIN_SCENARIO=vessel .venv/bin/python -m bridge.run_live_demo
"""

from __future__ import annotations

import os
import sys
import time

from bridge.bridge import Bridge
from bridge.plc_io import ModbusPlc
from common import registers as R
from common import scenarios
from physics import make_model
from plc.load_program import restart_runtime

DT = 1.0            # seconds of process time per cycle
SETTLE = 0.12       # real seconds per cycle, to let the PLCs scan (~100 ms task)
ATTACK_AT = 10.0    # process seconds of normal operation before the attack


def _row(sc, tag, s):
    status = "TRIPPED" if s.hmi_trip_status else "armed"
    flag = f"  <== {sc.failure_word}" if s.true_pv >= sc.fail_at else ""
    u = sc.pv_units
    return (f"{tag:7} t={s.time:6.1f}s  true={s.true_pv:7.3f}{u:3} "
            f"HMI={s.hmi_pv:7.3f}{u:3} [{status:7}]  feed={s.feed:3.0%}  "
            f"gap={s.divergence:6.3f}{u}{flag}")


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else \
        os.environ.get("PETROTWIN_SCENARIO", scenarios.DEFAULT_SCENARIO)
    sc = scenarios.get(sid)

    print("Resetting PLC runtimes for a clean run ...")
    restart_runtime("http://127.0.0.1:8081")
    restart_runtime("http://127.0.0.1:8082")

    bpcs = ModbusPlc.for_instance(R.Instance.BPCS)
    sis = ModbusPlc.for_instance(R.Instance.SIS)
    model = make_model(sid)
    b = Bridge(model, bpcs, sis, dt=DT)

    # Warm up a few cycles so the SIS has mirrored the PV to the HMI, then
    # restart the process clock so the printed run starts clean.
    for _ in range(4):
        b.step()
        time.sleep(SETTLE)
    b.model.reset()

    print(f"PetroTwin LIVE divergence demo — {sc.title} [{sc.unit_tag}] "
          "(real OpenPLC over Modbus)")
    print(f"SIS trip at {sc.trip_hh} {sc.pv_units}; "
          f"attack (SIS_OVERRIDE) injected at t={ATTACK_AT:.0f} s\n")

    attacked = False
    frozen = None
    last_print = -999.0
    s = None
    try:
        for _ in range(400):
            if not attacked and b.model.time >= ATTACK_AT:
                b.sis.write_coil(R.SisCoil.OVERRIDE, True)  # the attack, over Modbus
                attacked = True
                frozen = b.hmi_pv()
                print(f">>> ATTACK: SIS_OVERRIDE set — HMI frozen at "
                      f"{frozen:.3f} {sc.pv_units}\n")
            s = b.step()
            time.sleep(SETTLE)
            if s.time - last_print >= 10.0 - 1e-9 or b.model.failed:
                print(_row(sc, "ATTACK" if attacked else "normal", s))
                last_print = s.time
            if b.model.failed:
                break
    finally:
        b.close()

    ok = (b.model.failed and not s.hmi_trip_status
          and frozen is not None and abs(s.hmi_pv - frozen) < 0.01
          and s.divergence > 0.4 * sc.fail_at)
    print(f"\n  true={s.true_pv:.3f} {sc.pv_units} ({sc.failure_word}), "
          f"HMI={s.hmi_pv:.3f} {sc.pv_units} "
          f"({'armed' if not s.hmi_trip_status else 'tripped'}), "
          f"gap={s.divergence:.3f} {sc.pv_units}")
    print(f"  acceptance (live): {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
