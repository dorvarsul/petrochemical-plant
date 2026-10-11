# PetroTwin — Roadmap

Phased plan. **Each phase is a complete, defensible stopping point** — the project is
useful and demonstrable at the end of every phase, not only at the end.

## Phases

| Phase | Name                          | Effort        | Scope                                                                                                                                         | "Done" criteria                                                                                                            |
|-------|-------------------------------|---------------|----------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| **0** | **Demo (PROTECTED)**          | **2 working days** | **ONE** scenario. Python physics, OpenPLC control + safety, lightweight HMI, Three.js ground truth, Modbus bridge, attack script. **Modbus-only, one scenario.** | **Live run:** normal → inject attack → **HMI green while 3D goes to rupture.** (See `TESTING.md` acceptance test.)          |
| 0.5   | Multi-scenario (Python physics) | done        | All **3 scenarios** (tank overfill, vessel overpressure, reactor runaway) on pure-Python ODEs, selectable through every layer (physics, both PLCs, bridge, HMI/3D, attack). **Still Modbus-only; no OpenModelica.**            | Live run per scenario: normal → attack → HMI green while 3D passes the failure threshold. All three pass (see `TESTING.md`). |
| 1     | Physics realism               | 1–2 weeks     | OpenModelica / validated model, real units + geometry, relief sizing, sensor noise, actuator & transport dynamics (for the three scenarios that already exist). | Validated model reproduces known steady states; all three scenarios run to their respective failures with realistic timing.|
| 2     | Network / protocol realism    | ~1 week       | OPC-UA alongside Modbus, historian, engineering workstation, Purdue-level segmentation, attack **captured as pcap**.                           | Segmented network stands up; attack traverses it and is captured as a replayable pcap.                                     |
| 3     | Triton-mechanism realism      | 1–2 weeks     | Emulate a proprietary TriStation-like **safety protocol** so the attack exploits a *protocol*, not just Modbus register writes.                | Attack succeeds by exploiting the emulated safety protocol, not by direct register writes.                                 |
| —     | **Out of scope (separate project)** | months; hardware + legal | Real or virtualized **Triconex** hardware + original malware analysis.                                                                 | The realism ceiling. Opened **only as a funded decision**, not as a default next step.                                     |

## The protected 2-day demo

Phase 0 is **protected**. Its purpose is one convincing end-to-end run, fast. It is
deliberately minimal:

**In scope for the 2 days**
- One scenario only (anchor scenario to be chosen before coding).
- Python ODE physics (no FMI, no OpenModelica).
- Two OpenPLC instances (Control + Safety) with minimal logic.
- One Modbus bridge loop.
- Lightweight HMI polling Modbus.
- Three.js ground-truth view off the physics directly.
- One attack script (override SIS trip + freeze HMI registers).

**Out of scope for the 2 days** (defer to Phase 1+)
- The other two scenarios. *(Protected during the demo; added immediately after as
  Phase 0.5 on Python ODE physics — the demo itself stayed one scenario. OpenModelica
  realism for them is still Phase 1.)*
- OpenModelica / FMI / validated physics, real units & geometry, relief sizing.
- Sensor noise, actuator/transport dynamics.
- OPC-UA, historian, engineering workstation, network segmentation, pcap capture.
- Any TriStation/Triconex mechanism fidelity.

## Biggest risk throughout

> The single biggest technical risk is **four-process time-synchronization over the
> Modbus bridge** (physics ↔ Control PLC ↔ Safety PLC ↔ HMI, plus the 3D tap) — **not
> any single layer**. If the loops drift, the demo's cause-and-effect becomes
> unreadable.

**Mitigation = scope discipline.** Protect Phase 0 by **resisting all additions**
(FMI, OPC-UA, extra scenarios, richer physics) until Phase 1+. Every feature added
before the end-to-end loop is stable multiplies the sync surface and endangers the
demo. Get one scenario looping cleanly first; add realism only after.
