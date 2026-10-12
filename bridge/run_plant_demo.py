"""End-to-end CONNECTED-PLANT demo using the fake PLCs (no Docker required).

Runs the whole flowsheet at once — T-101 feed tank -> R-301 reactor -> V-201
vessel — through the ``PlantBridge`` with in-memory PLCs (one BPCS + one SIS per
unit). It shows the Phase-1 story: the plant holds a connected steady state,
then an attack on ONE chosen unit drives that unit to failure while its operator
HMI stays frozen and "armed", and the up/downstream units respond through the
forward couplings (an overfilling tank warms the reactor; a runaway reactor
pressurises the vessel, whose own healthy SIS then protects it).

Run:  python3 -m bridge.run_plant_demo [tank|reactor|vessel]
      (the argument chooses which unit the attacker targets; default: reactor)
"""

from __future__ import annotations

import sys

from bridge.plant_bridge import make_fake_plant_bridge
from physics.plant import FLOW

DT = 0.1
ATTACK_AT = 10.0      # seconds of normal operation before the attack
PRINT_EVERY = 20.0    # seconds between printed rows
DEFAULT_TARGET = "reactor"
MAX_SECONDS = 500.0


def _unit_cell(u) -> str:
    status = "TRIP" if u.hmi_trip_status else "armed"
    flag = "!" if u.failed else " "
    return (f"{u.tag}{flag} true={u.true_pv:7.2f}{u.pv_units:3} "
            f"HMI={u.hmi_pv:7.2f}{u.pv_units:3} {status:5}")


def _row(tag: str, s) -> str:
    cells = "  |  ".join(_unit_cell(s.units[uid])
                         for uid in ("tank", "reactor", "vessel"))
    return f"{tag:7} t={s.time:6.1f}s   {cells}"


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET
    if target not in FLOW:
        print(f"unknown unit {target!r}; choose {'|'.join(FLOW)}")
        sys.exit(2)
    b = make_fake_plant_bridge(dt=DT)

    tgt_sc = b.plant.unit(target).scenario
    print("PetroTwin CONNECTED-PLANT demo (fake PLCs)")
    print("Flowsheet:  T-101 feed tank  ->  R-301 reactor  ->  V-201 vessel")
    print(f"Attacker targets: {target}  [{tgt_sc.unit_tag}] — forces its BPCS "
          f"setpoint off-scale + SIS_OVERRIDE at t={ATTACK_AT:.0f}s.")
    print(f"Watch the other units respond through the forward couplings.\n")

    attacked = False
    frozen = None
    last_print = -999.0
    fail_announced = False
    neigh_trip_announced = False
    fail_time = None
    s = None
    for _ in range(int(MAX_SECONDS / DT)):
        if not attacked and b.plant.time >= ATTACK_AT:
            frozen = b.hmi_pv(target)
            b.attack(target)
            attacked = True
            print(f">>> ATTACK on {target}: BPCS forced to MANUAL, {tgt_sc.actuator} "
                  f"-> {tgt_sc.attack_manual_out_reg/10:g}% + SIS_OVERRIDE; "
                  f"HMI frozen at {frozen:.2f} {tgt_sc.pv_units}\n")

        s = b.step()

        # Print at a fixed cadence, plus a one-off row the moment the target
        # fails and the moment a neighbour's (healthy) SIS trips in response.
        tgt_failed = s.units[target].failed
        neigh_tripped = any(u.trip_cmd for k, u in s.units.items() if k != target)
        milestone = ((tgt_failed and not fail_announced)
                     or (neigh_tripped and not neigh_trip_announced))
        if s.time - last_print >= PRINT_EVERY - 1e-9 or milestone:
            note = ""
            if tgt_failed and not fail_announced:
                note = f"   <-- {target} past {tgt_sc.failure_word}"
                fail_announced = True
                fail_time = s.time
            if neigh_tripped and not neigh_trip_announced:
                trn = [k for k, u in s.units.items()
                       if k != target and u.trip_cmd]
                note += f"   <-- neighbour SIS trip: {', '.join(trn)} (held safe)"
                neigh_trip_announced = True
            print(_row("ATTACK" if attacked else "normal", s) + note)
            last_print = s.time

        # Stop once the target has failed and either a neighbour's SIS has
        # caught its response, or a settle window has elapsed.
        if fail_time is not None and (neigh_trip_announced
                                      or s.time - fail_time > 90.0):
            break

    print("\nResult (ground truth vs. operator HMI):")
    for uid in ("tank", "reactor", "vessel"):
        u = s.units[uid]
        mark = "  <== attacked, FAILED" if (uid == target and u.failed) else (
            "  (responded)" if abs(u.true_pv - b.plant.unit(uid).scenario.setpoint)
            > 0.5 and uid != target else "")
        verdict = ("FAILED/" + b.plant.unit(uid).scenario.failure_word
                   if u.failed else
                   ("TRIPPED-safe" if u.hmi_trip_status or u.trip_cmd else "holding"))
        print(f"  {uid:7} {u.tag:6}: true={u.true_pv:7.2f}{u.pv_units:3} "
              f"HMI={u.hmi_pv:7.2f}{u.pv_units:3} [{verdict}]{mark}")

    tgt = s.units[target]
    ok = (tgt.failed and not tgt.hmi_trip_status and frozen is not None
          and abs(tgt.hmi_pv - frozen) < 1e-6
          and tgt.divergence > 0.4 * tgt_sc.fail_at
          and all(not u.failed for k, u in s.units.items() if k != target))
    print(f"\n  acceptance (target failed + HMI frozen/armed + large gap + "
          f"no neighbour failure): {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
