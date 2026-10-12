"""End-to-end divergence demo using the fake PLCs (no Docker required).

Runs any of the three scenarios through the real Bridge loop, but with the
in-memory PLCs: normal operation, then the SIS attack injected, then the
process runs to failure while the operator HMI stays frozen and "armed". This
is the acceptance story (TESTING.md section 5) made visible without the live
stack.

Run:  python3 -m bridge.run_fake_demo [tank|vessel|reactor]
      PETROTWIN_SCENARIO=reactor python3 -m bridge.run_fake_demo
"""

from __future__ import annotations

import os
import sys

from bridge.bridge import Bridge
from bridge.plc_io import FakeBpcs, FakeSis
from common import registers as R
from common import scenarios
from physics import make_model

DT = 0.1
ATTACK_AT = 10.0  # seconds of normal operation before the attack is injected


def _pick_scenario(argv) -> str:
    if len(argv) > 1:
        return argv[1]
    return os.environ.get("PETROTWIN_SCENARIO", scenarios.DEFAULT_SCENARIO)


def _row(sc, tag: str, s) -> str:
    status = "TRIPPED" if s.hmi_trip_status else "armed"
    flag = f"  <== {sc.failure_word}" if s.true_pv >= sc.fail_at else ""
    u = sc.pv_units
    return (f"{tag:7} t={s.time:6.1f}s  true={s.true_pv:7.3f}{u:3} "
            f"HMI={s.hmi_pv:7.3f}{u:3} [{status:7}]  "
            f"feed={s.feed:3.0%}  gap={s.divergence:6.3f}{u}{flag}")


def main() -> None:
    sid = _pick_scenario(sys.argv)
    sc = scenarios.get(sid)
    model = make_model(sid)
    b = Bridge(model, FakeBpcs(sc), FakeSis(sc.trip_reg), dt=DT)
    b.prime_setpoint()  # operator's safe setpoint; baseline holds here until the attack

    print(f"PetroTwin divergence demo (fake PLCs) — {sc.title} [{sc.unit_tag}]")
    print(f"PV = {sc.pv_name} ({sc.pv_units}); BPCS holds setpoint {sc.setpoint} "
          f"{sc.pv_units}; SIS trip at {sc.trip_hh} {sc.pv_units}; "
          f"{sc.failure_word} at {sc.fail_at} {sc.pv_units}; "
          f"attack at t={ATTACK_AT:.0f} s\n")

    attacked = False
    frozen_hmi = None
    last_print = -999.0
    s = None
    for _ in range(40000):
        if not attacked and b.model.time >= ATTACK_AT:
            # The external attack, over Modbus: manipulate the control setpoint
            # AND defeat the safety layer.
            b.bpcs.write_holding(R.BpcsHolding.SETPOINT, sc.attack_setpoint_reg)
            b.sis.write_coil(R.SisCoil.OVERRIDE, True)
            attacked = True
            frozen_hmi = b.hmi_pv()
            atk_sp = sc.reg_to_pv(sc.attack_setpoint_reg)
            print(f">>> ATTACK: BPCS setpoint forced to {atk_sp:g} {sc.pv_units} "
                  f"(safe {sc.setpoint:g}) + SIS_OVERRIDE set — HMI now frozen at "
                  f"{frozen_hmi:.3f} {sc.pv_units}, trip suppressed\n")

        s = b.step()
        if s.time - last_print >= 10.0 - 1e-9 or b.model.failed:
            print(_row(sc, "ATTACK" if attacked else "normal", s))
            last_print = s.time
        if b.model.failed:
            break

    gap_needed = 0.4 * sc.fail_at  # a large, unmistakable spoof gap
    print("\nResult:")
    print(f"  true {sc.pv_name:11}: {s.true_pv:.3f} {sc.pv_units} "
          f"({sc.failure_word if b.model.failed else 'no failure'})")
    print(f"  operator HMI shows : {s.hmi_pv:.3f} {sc.pv_units} "
          f"({'TRIPPED' if s.hmi_trip_status else 'armed — looks normal'})")
    print(f"  divergence (gap)   : {s.divergence:.3f} {sc.pv_units}")
    ok = (b.model.failed and not s.hmi_trip_status
          and frozen_hmi is not None and abs(s.hmi_pv - frozen_hmi) < 1e-6
          and s.divergence > gap_needed)
    print(f"\n  acceptance (failure + HMI frozen/armed + gap > "
          f"{gap_needed:g} {sc.pv_units}): {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
