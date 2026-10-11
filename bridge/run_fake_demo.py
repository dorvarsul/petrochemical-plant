"""End-to-end divergence demo using the fake PLCs (no Docker required).

Runs the Phase-0 tank-overfill scenario through the real Bridge loop, but with
the in-memory PLCs: normal fill, then the SIS attack injected, then the tank
runs to overflow while the operator HMI stays frozen and "armed". This is the
acceptance story (TESTING.md section 5) made visible without the live stack.

Run:  python3 -m bridge.run_fake_demo
"""

from __future__ import annotations

from bridge.bridge import Bridge
from bridge.plc_io import FakeBpcs, FakeSis
from common import registers as R
from physics.tank import TankModel

DT = 0.1
ATTACK_AT = 10.0  # seconds of normal operation before the attack is injected


def _row(tag: str, s) -> str:
    status = "TRIPPED" if s.hmi_trip_status else "armed"
    flag = "  <== OVERFLOW" if s.true_level >= 10.0 else ""
    return (f"{tag:7} t={s.time:6.1f}s  true={s.true_level:6.3f} m  "
            f"HMI={s.hmi_level:6.3f} m [{status:7}]  "
            f"feed={s.feed:3.0%}  gap={s.divergence:5.3f} m{flag}")


def main() -> None:
    tank = TankModel(q_out=0.0, level0=5.0)
    b = Bridge(tank, FakeBpcs(), FakeSis(), dt=DT)

    print("PetroTwin Phase-0 divergence demo (fake PLCs)")
    print(f"Tank: height_full={tank.height_full} m, SIS trip at 9.0 m, "
          f"attack injected at t={ATTACK_AT:.0f} s\n")

    attacked = False
    frozen_hmi = None
    last_print = -999.0
    s = None
    for _ in range(4000):
        if not attacked and b.tank.time >= ATTACK_AT:
            b.sis.write_coil(R.SisCoil.OVERRIDE, True)  # <-- the attack
            attacked = True
            frozen_hmi = b.hmi_level()
            print(f">>> ATTACK: SIS_OVERRIDE set — HMI now frozen at "
                  f"{frozen_hmi:.3f} m, trip suppressed\n")

        s = b.step()
        if s.time - last_print >= 10.0 - 1e-9 or b.tank.overflowing:
            print(_row("ATTACK" if attacked else "normal", s))
            last_print = s.time
        if b.tank.overflowing:
            break

    print("\nResult:")
    print(f"  true level reached   : {s.true_level:.3f} m "
          f"({'OVERFLOW' if b.tank.overflowing else 'no overflow'})")
    print(f"  operator HMI shows   : {s.hmi_level:.3f} m "
          f"({'TRIPPED' if s.hmi_trip_status else 'armed — looks normal'})")
    print(f"  divergence (gap)     : {s.divergence:.3f} m")
    ok = (b.tank.overflowing and not s.hmi_trip_status
          and frozen_hmi is not None and abs(s.hmi_level - frozen_hmi) < 1e-6
          and s.divergence > 4.0)
    print(f"\n  acceptance (overflow + HMI frozen/armed + gap > 4 m): "
          f"{'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
