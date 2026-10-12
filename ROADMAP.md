# PetroTwin — Roadmap

Phased plan. **Each phase is a complete, defensible stopping point** — the project is
useful and demonstrable at the end of every phase, not only at the end.

## Phases

| Phase | Name                          | Effort        | Scope                                                                                                                                         | "Done" criteria                                                                                                            |
|-------|-------------------------------|---------------|----------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| **0** | **Demo (PROTECTED)**          | done (2 working days) | **ONE** scenario. Python physics, OpenPLC control + safety, lightweight HMI, Three.js ground truth, Modbus bridge, attack script. **Modbus-only, one scenario.** | **Live run:** normal → inject attack → **HMI green while 3D goes to rupture.** (See `TESTING.md` acceptance test.)          |
| 0.5   | Multi-scenario (Python physics) | done        | All **3 scenarios** (tank overfill, vessel overpressure, reactor runaway) on pure-Python ODEs, selectable through every layer (physics, both PLCs, bridge, HMI/3D, attack). Three **independent single-unit** sims — one runs at a time. **Still Modbus-only; no OpenModelica.**            | Live run per scenario: normal → attack → HMI green while 3D passes the failure threshold. All three pass (see `TESTING.md`). |
| **1** | **Connected plant (single flowsheet)** | core done (Python/fake-PLC path) | Couple the three units into **ONE continuously-running plant**: `T-101` feed tank → `R-301` reactor → `V-201` separation vessel, with shared streams (tank outflow **is** the reactor feed; reactor product **is** the vessel inflow). Plausible hand-tuned physics whose mass/energy balances are consistent **across** the couplings — still Python ODEs, still Modbus-only. Each "scenario" is reframed as **which unit the attack targets** inside the one plant, not a separate sim. *(Live OpenPLC multi-unit stack + register-map extension folded into Phase 2.)* | **Met** on the Python + in-memory-PLC path: `physics/plant.py` + `bridge/plant_bridge.py`, proven by `tests/test_plant.py` (criteria P1–P7 in `TESTING.md`) and shown by `bridge.run_plant_demo`. No attacker → connected steady state, no trips; attacking a unit drives **that** unit past failure while neighbours respond plausibly (forward coupling) and a healthy SIS holds the manual-drive-with-SIS-armed case safe. The BPCS/SIS now carry real-SIF realism (setpoint clamp, AUTO/MANUAL + rate limit, pre-alarm/bad-PV/deadband reset, maintenance bypass), the attack is driven through MANUAL mode, and the terminal FILL/EXPORT valves are real coils with a P-401 level sink. |
| **2** | **Whole-plant visualization + live stack** | done | HMI + Three.js show the **entire connected flowsheet at once** (all three units + the interconnecting streams), not one unit in isolation. Streams, levels, pressures and temperatures animate from the physics; the attacked unit is highlighted; spoofable HMI vs unspoofable 3D ground truth span the whole plant. The whole-plant visualization runs on **both** the dependency-free fake-PLC path and the **live OpenPLC pair** — `ui/plant_server.py` + `ui/static/plant.html` (`./run_plant.sh start`, or `python3 -m ui.plant_server [--live]`): a three-tile spoofable HMI strip over one unspoofable 3D ground-truth train, attack any unit, couplings animated. The live stack carries all three units on the **two existing slaves** — one BPCS for all three units' control, one SIS for all three units' safety — via per-unit **offset blocks** (`registers.unit_addr`) and one generated **3-instance ST program** per slave (`plc.load_scenario.load_plant`); no extra containers. | Operator watches the full plant live; during an attack the targeted unit runs to failure in the 3D ground truth while its HMI tile stays green, **and** the propagation to neighbouring units is visible. **Met** on the fake-PLC path and on real OpenPLC (`tests/test_plant_live.py`). |
| **2.5** | **Distributed deployment (4-PC, L2)** | planned (changes ahead) | Move off a single host: run the stack as **four physical Ubuntu Linux PCs on one layer-2 Ethernet segment** — (1) **Physics + 3D** (ground-truth sim and the Three.js view, kept together so the direct physics tap stays a *local* shared-state read), (2) **Control PLC / BPCS**, (3) **Safety PLC / SIS**, (4) **HMI**. The Modbus bridge runs on the physics PC and reaches the two PLC hosts over **Modbus TCP across the L2** instead of `localhost`; the HMI polls the PLC hosts over the same L2. No change to layers, logic, register map or scenarios — this is a **deployment-topology** change, still Modbus-only. See *Distributed deployment* below. | Full normal → attack → (HMI green while 3D passes threshold) run reproduced with each layer on its own PC over the L2; the direct physics→3D tap never crosses the network; the attacker runs as a **real host on the L2** reaching the PLCs over Modbus TCP. |
| 3     | Network / protocol realism    | ~1 week       | OPC-UA alongside Modbus, historian, engineering workstation, Purdue-level segmentation, attack **captured as pcap**.                           | Segmented network stands up; attack traverses it and is captured as a replayable pcap.                                     |
| 4     | Triton-mechanism realism      | 1–2 weeks     | Emulate a proprietary TriStation-like **safety protocol** so the attack exploits a *protocol*, not just Modbus register writes.                | Attack succeeds by exploiting the emulated safety protocol, not by direct register writes.                                 |
| **5** | **Physics migration → OpenModelica (FINAL)** | 1–2 weeks | Replace the hand-tuned Python ODEs with **validated OpenModelica / FMI** models of the **same connected flowsheet**: real units + geometry, relief sizing, sensor noise, actuator & transport dynamics. Swapped **behind the existing `model.step(dt, feed_cmd, trip)` interface** so the bridge / PLC / HMI / 3D layers are unchanged. | Validated connected model reproduces known steady states; all three target scenarios run to failure with realistic timing; **no change required above the physics layer.** |
| —     | **Out of scope (separate project)** | months; hardware + legal | Real or virtualized **Triconex** hardware + original malware analysis.                                                                 | The realism ceiling. Opened **only as a funded decision**, not as a default next step.                                     |

## The connected plant (Phase 1–2 target)

Phase 0.5 left three **independent** single-unit sims selectable one at a time.
Phases 1–2 fuse them into **one plant that always runs whole**, with the three
hazards sitting on three different units of a single, plausible petrochemical
flowsheet.

> **Status.** The connected *physics and behaviour* (this section) are
> implemented on the Python + in-memory-PLC path — `physics/plant.py`,
> `bridge/plant_bridge.py`, `bridge/run_plant_demo.py`, `tests/test_plant.py`.
> The **whole-plant visualization** runs on that path and on the **live OpenPLC
> pair** — `ui/plant_server.py` + `ui/static/plant.html` (`./run_plant.sh start`
> or `python3 -m ui.plant_server --live`). The live stack carries all three units
> on the two existing slaves (one BPCS + one SIS, per-unit offset blocks +
> generated 3-instance ST programs; `bridge.plc_io.ModbusUnitView`,
> `plc.load_scenario.load_plant`), proven by `tests/test_plant_live.py`. Phase 2
> is complete; OpenModelica (Phase 5) is the final phase.

```
            feed                 product                 vapour/overpressure
  ┌────────┐  flow   ┌─────────┐  flow    ┌──────────┐    relief
  │ T-101  ├────────►│  R-301  ├─────────►│  V-201   ├────────►
  │  feed  │         │ reactor │          │separation│
  │  tank  │         │  (CSTR) │          │  vessel  │
  └────────┘         └─────────┘          └──────────┘
   overfill          runaway               overpressure
  (level SIS)       (cooling SIS)          (relief SIS)
```

- **Shared streams couple the ODEs.** The tank's downstream draw (today a
  *constant* `q_out`) becomes the **reactor feed**; the reactor's product
  outflow becomes the **vessel inflow**. So a disturbance in one unit propagates
  to its neighbours — the behaviour that makes a *connected* plant worth
  building, and the behaviour the 3D view must show.
- **Each scenario targets a different unit, not a different sim.** `tank`
  attacks the `T-101` level loop + high-level trip; `reactor` attacks the
  `R-301` cooling loop + high-temperature trip; `vessel` attacks the `V-201`
  pressure loop + relief trip. The whole plant keeps running around the
  targeted unit. `common/scenarios.py` stays the per-unit source of truth; what
  changes is that the units now exchange streams instead of standing alone.
- **Plausible, not validated.** Phase 1 physics stays hand-tuned Python ODEs,
  chosen so the couplings balance at a believable steady state and failures
  arrive on a legible timescale. *Validated* physics is deliberately deferred to
  the final OpenModelica phase (below) — "plausible now, validated last".
- **The physics interface does not change.** Everything above stays behind
  `model.step(dt, feed_cmd, trip)` (see `physics/base.py`), which is exactly
  what lets Phase 5 swap the Python ODEs for OpenModelica/FMI with no change to
  the bridge, PLCs, HMI or 3D.

## Distributed deployment (Phase 2.5 target)

Phases 0–2 run every layer as co-operating processes on **one host** (`localhost`
Modbus ports 5020/5021, the 3D view reading physics via in-process shared
state/IPC). Phase 2.5 keeps that exact software and splits it across **four
physical PCs on a single layer-2 Ethernet segment**, all running Ubuntu Linux:

```
                    ┌──────────────────────────────────────────┐
                    │  PC1 — PHYSICS + 3D                        │
                    │  Python ODE plant (TRUE STATE)             │
                    │  Three.js ground truth (local tap)         │
                    │  Modbus bridge = Modbus TCP MASTER         │
                    └───────┬───────────────────────┬────────────┘
                            │ Modbus TCP            │ Modbus TCP
                   ┌────────┴─────────┐    ┌────────┴──────────┐
                   ▼                  │    ▼                   │
        ┌─────────────────┐          │ ┌─────────────────┐    │
        │ PC2 — CONTROL   │          │ │ PC3 — SAFETY    │    │
        │ PLC / BPCS      │          │ │ PLC / SIS       │    │
        │ OpenPLC slave   │          │ │ OpenPLC slave   │    │
        └─────────────────┘          │ └─────────────────┘    │
                   ▲                  │          ▲             │
                   └──────────────────┴──────────┴─────────────┘
                              L2 Ethernet switch
                                     ▲
                            ┌────────┴─────────┐
                            │ PC4 — HMI        │
                            │ Modbus TCP master│
                            │ (polls PC2, PC3) │
                            └──────────────────┘
```

- **One PC per layer.** PC1 = Physics + 3D; PC2 = Control PLC (BPCS); PC3 =
  Safety PLC (SIS); PC4 = HMI. All four are Ubuntu Linux hosts on one **L2**
  (single broadcast domain, no routing/segmentation yet — segmentation is still
  Phase 3).
- **Physics and 3D stay on the same PC — on purpose.** The core invariant is
  that the 3D ground-truth view reads the physics model **directly** (shared
  state/IPC), never over the network. Co-locating them on PC1 keeps that tap a
  *local* read, so it remains **unspoofable** by anything on the wire. Putting
  the 3D view on its own PC would force the ground truth across the L2 and break
  the invariant — so it is explicitly **not** done.
- **`localhost` → real IPs, same register map.** The bridge on PC1 opens Modbus
  **TCP** connections to PC2 and PC3 by IP:502 (or the chosen port) instead of
  `localhost:5020/5021`; the HMI on PC4 polls the same two hosts. Nothing else
  moves: the four layers, the two-PLC split, the two data taps, the register map
  (`common/registers.py`, including the per-unit offset blocks), the scenarios
  and the attack logic are all unchanged. The only new configuration is a
  **host/IP map** (which layer lives at which address) replacing hard-coded
  `localhost`.
- **The attacker becomes a real host on the L2.** Today the attack script is
  another local process; distributed, it runs from a machine on the same L2
  segment and reaches the PLCs over Modbus TCP — a faithful on-network adversary,
  which is why this is a worthwhile step even before segmentation/pcap (Phase 3).
- **Still Modbus-only; still hand-tuned Python physics.** No OPC-UA, no
  OpenModelica, no new scenarios. This phase changes *where the processes run*,
  not *what they do*.

## Why OpenModelica is last, not first

Validated physics is the highest-fidelity goal but also the heaviest
integration, and it is **not on the critical path** to a connected,
watchable plant. Building the flowsheet + whole-plant visualization first on
hand-tuned Python ODEs gives a demonstrable, end-to-end connected plant quickly;
the OpenModelica migration then drops in **behind the unchanged `step()`
interface** as a pure physics-layer swap. Doing it in the other order would
couple the hardest dependency to the earliest milestone for no demo benefit.

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
  Phase 0.5 on Python ODE physics — the demo itself stayed one scenario. Connecting
  them into one flowsheet is Phase 1; validated OpenModelica physics is the final
  phase.)*
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

**New risk from Phase 2.5 onward — real-network time-sync.** Distributing the four
layers onto separate PCs turns the in-host sync loop into one that crosses a real
L2 segment: added link latency/jitter, independent machine clocks, and no shared
process scheduler. This is the single-host time-sync risk amplified, not a new
class of problem. Mitigation: a wired, lightly-loaded L2 switch; a common time
base across the four PCs (NTP, or PTP if drift shows); keep the bridge/HMI poll
periods comfortably above the observed round-trip; and re-run the connected
steady-state acceptance test over the network **before** injecting any attack.

**New risk from Phase 1 onward — cross-unit coupling.** Once the three units share
streams, a disturbance (or an attack) on one unit propagates to its neighbours.
That propagation is the *point* of a connected plant, but it also makes
cause-and-effect harder to read and widens the time-sync surface (the single-unit
sims never interacted). Mitigation: keep the plant at a clearly-settled connected
steady state before injecting any attack, make the targeted unit visually obvious
in the whole-plant view, and add a connected-steady-state acceptance test
(no-attacker → every unit holds, no SIS trips) **before** layering the attacks on.
