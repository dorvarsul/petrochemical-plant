"""Live end-to-end divergence demo over REAL Modbus + OpenPLC.

Same scenario as run_fake_demo, but the Bridge drives the two real OpenPLC v3
slaves via pymodbus. This exercises the actual time-sync surface (physics clock
vs. the PLC scan cycle over Modbus) — the #1 Phase-0 risk.

Prerequisites: both OpenPLC containers up with bpcs.st / sis.st loaded and
running (plc/README.md, or plc/load_program.py). Needs pymodbus (requirements.txt).

Run:  .venv/bin/python -m bridge.run_live_demo
"""

from __future__ import annotations

import time

from bridge.bridge import Bridge
from bridge.plc_io import ModbusPlc
from common import registers as R
from physics.tank import TankModel
from plc.load_program import restart_runtime

DT = 1.0            # seconds of process time per cycle
SETTLE = 0.12       # real seconds per cycle, to let the PLCs scan (~100 ms task)
ATTACK_AT = 10.0    # process seconds of normal operation before the attack


def _row(tag, s):
    status = "TRIPPED" if s.hmi_trip_status else "armed"
    flag = "  <== OVERFLOW" if s.true_level >= 10.0 else ""
    return (f"{tag:7} t={s.time:6.1f}s  true={s.true_level:6.3f} m  "
            f"HMI={s.hmi_level:6.3f} m [{status:7}]  feed={s.feed:3.0%}  "
            f"gap={s.divergence:5.3f} m{flag}")


def main():
    print("Resetting PLC runtimes for a clean run ...")
    restart_runtime("http://127.0.0.1:8081")
    restart_runtime("http://127.0.0.1:8082")

    bpcs = ModbusPlc.for_instance(R.Instance.BPCS)
    sis = ModbusPlc.for_instance(R.Instance.SIS)
    tank = TankModel(q_out=0.0, level0=5.0)
    b = Bridge(tank, bpcs, sis, dt=DT)

    # Warm up a few cycles so the SIS has mirrored the level to the HMI, then
    # restart the process clock so the printed run starts clean.
    for _ in range(4):
        b.step()
        time.sleep(SETTLE)
    b.tank.reset()

    print("PetroTwin LIVE divergence demo (real OpenPLC over Modbus)")
    print(f"SIS trip at 9.0 m; attack (SIS_OVERRIDE) injected at t={ATTACK_AT:.0f} s\n")

    attacked = False
    frozen = None
    last_print = -999.0
    s = None
    try:
        for _ in range(200):
            if not attacked and b.tank.time >= ATTACK_AT:
                b.sis.write_coil(R.SisCoil.OVERRIDE, True)  # the attack, over Modbus
                attacked = True
                frozen = b.hmi_level()
                print(f">>> ATTACK: SIS_OVERRIDE set — HMI frozen at {frozen:.3f} m\n")
            s = b.step()
            time.sleep(SETTLE)
            if s.time - last_print >= 10.0 - 1e-9 or b.tank.overflowing:
                print(_row("ATTACK" if attacked else "normal", s))
                last_print = s.time
            if b.tank.overflowing:
                break
    finally:
        b.close()

    ok = (b.tank.overflowing and not s.hmi_trip_status
          and frozen is not None and abs(s.hmi_level - frozen) < 0.01
          and s.divergence > 4.0)
    print(f"\n  true={s.true_level:.3f} m (OVERFLOW), HMI={s.hmi_level:.3f} m "
          f"({'armed' if not s.hmi_trip_status else 'tripped'}), gap={s.divergence:.3f} m")
    print(f"  acceptance (live): {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
