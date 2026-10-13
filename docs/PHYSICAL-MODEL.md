# PetroTwin — The Physical Model & the OT Facility

This document explains, in one place, **what PetroTwin physically represents** and
**what kind of OT (operational-technology) facility it is**. It is the "what is
this a model *of*" companion to the structural docs: see
[ARCHITECTURE.md](ARCHITECTURE.md) for the register map and process topology,
[ROADMAP.md](ROADMAP.md) for the phase plan, and [ATTACKS.md](ATTACKS.md) for the
attack detail.

> **One-liner.** PetroTwin is a *software* OT-security testbed: a small simulated
> petrochemical plant wired up like a real one — a true-physics process, two
> PLCs (a control PLC and a safety PLC) talking Modbus, a control-room HMI, and a
> 3D "ground-truth" view — built so we can demonstrate a **Triton-style attack
> *pattern*** (defeat the safety system, blind the operator, run the process to
> failure) for **defensive research and training**. There is no real plant and no
> real malware.

---

## 1. What kind of facility is this?

PetroTwin models the **instrumentation and control (I&C) stack of a petrochemical
process unit** — the part of a plant that an OT-security engineer cares about. A
real facility of this kind has four conceptual layers; PetroTwin reproduces all
four as co-operating software processes (optionally spread across four PCs behind
one layer-3 routed switch, each layer in its own VLAN — see [DEPLOY.md](DEPLOY.md)):

| Layer | Real facility | PetroTwin | Trust |
|------|----------------|-----------|-------|
| **1. Process / physics** | The actual tanks, reactor, vessel, their fluids and reactions | Python ODE models holding the **true** state | ground truth |
| **2. PLCs** | A **BPCS** (Basic Process Control System) that regulates the process, and a separate **SIS** (Safety Instrumented System) that trips it to a safe state | Two OpenPLC instances — one Control PLC (BPCS), one Safety PLC (SIS) — running IEC-61131 Structured Text | the SIS is the **attack target** |
| **3. HMI / SCADA** | The operator's control-room screens, polling the PLCs over a fieldbus | A lightweight web HMI polling the PLCs over **Modbus** | **spoofable** (only sees what the PLCs report) |
| **4. Engineering / 3D view** | (PetroTwin-specific) a window onto physical reality | A Three.js view driven **directly** from the physics | **unspoofable** |

Two things make this a faithful OT target rather than a toy:

- **The BPCS and the SIS are genuinely separate.** Safety is an independent
  protection layer, exactly as in a real plant — which is why an attacker who
  wants to cause damage must specifically defeat the SIS, not just the control
  loop. This is the Triton lesson.
- **There are two data taps with different trust.** The HMI reads the process
  *through the PLCs* (so it shows whatever the PLCs are told/allowed to report),
  while the 3D view reads the physics *directly* (so it shows what is really
  happening). The gap between them, under attack, is the whole point of the
  testbed.

### Honest scope (what it is *not*)

- It demonstrates the Triton **pattern**, not its **mechanism**. Real Triton
  targeted **Triconex** safety controllers via the proprietary **TriStation**
  protocol; PetroTwin uses OpenPLC and plain **Modbus** register writes. We do
  not emulate Triconex firmware or TriStation. (Protocol-level realism is a later
  roadmap phase.)
- The physics is **plausible, not validated**. The ODEs are hand-tuned so the
  plant holds a believable steady state and fails on a legible timescale (~2–3
  min), not fitted to real plant data. Validated physics is the final phase
  (OpenModelica — see [ROADMAP.md](ROADMAP.md)).
- It is **Modbus-only** today. No OPC-UA, historian, or network segmentation yet.

---

## 2. The physical process (the plant)

PetroTwin is **one connected flowsheet**, in process-flow order:

```
   crude      T-101           R-301            V-201           P-401
   feed  ──▶  feed tank  ──▶  reactor   ──▶   gas vessel  ──▶  product  ──▶ export
             (level)         (CSTR, temp)     (pressure)      storage
             OVERFILL        RUNAWAY          OVERPRESSURE    (sink, no SIS)
                                               │ relief
                                               ▼ flare
```

Each process unit is a continuously-running model that holds its own true state.
The three hazard units (T-101, R-301, V-201) each have a BPCS control loop and an
SIS trip; **P-401** is a terminal product-storage **sink** with level alarms but
no safety trip and is never an attack target.

### 2.1 The units and their equations

Defined in `../common/scenarios.py` (the single source of per-unit truth) and
integrated in `../physics/`:

- **T-101 — feed tank (overfill).** One state: liquid **level** `L`. A steady
  downstream draw `q_out` must be replaced by the feed valve:
  `dL/dt = (q_in·feed − q_out) / area`. Hazard: feed driven open → level rises to
  the brim → **OVERFLOW**.

- **R-301 — reactor (runaway).** A CSTR with Arrhenius self-heating; two coupled
  states, **concentration** `C` and **temperature** `T`:
  ```
  k(T)  = k0·exp(−E/(T+273.15))        (rate constant)
  r     = k(T)·C                        (reaction rate)
  dC/dt = q·(C_in − C) − r
  dT/dt = β·r − cool·(T − Tc)
  ```
  The reactant throughput `q` is **fixed**; the BPCS modulates the **coolant
  valve** (so its control gain is *negative* — open the coolant when too hot).
  Hazard: coolant valve driven shut → exotherm wins → thermal **RUNAWAY**.

- **V-201 — gas / pressure vessel (overpressure).** One state: **pressure** `P`:
  `dP/dt = k_in·feed − k_out − k_relief·trip`. A compressor feeds gas in; the SIS
  trip opens a relief valve (PSV) that vents to the flare. Hazard: feed driven up
  with relief defeated → pressure climbs → **RUPTURE**.

  > **V-201 is modelled as a gas (pressure) vessel, not a separator.** Its only
  > state is pressure, so it does not actually perform a vapour/liquid phase
  > split. A real petrochemical train needs genuine separation; adding a faithful
  > separator unit is deliberately deferred to the OpenModelica phase (see
  > *Add a separator* in [ROADMAP.md](ROADMAP.md)). Until then V-201 stays an
  > honest single-state gas vessel.

- **P-401 — product storage (sink).** One **level** state, filled by V-201
  bottoms and drained through an operator **EXPORT** valve; `q_export == q_fill`
  so an open export holds the level steady. LO/LO-LO/HI/HI-HI alarms, no trip.

### 2.2 How the units are connected (forward coupling)

The units exchange streams, so a disturbance **propagates forward** (downstream).
Each coupling is a gain times the *upstream* unit's **deviation above its own
setpoint** (`../physics/plant.py`):

```
dev_up      = max(0, (pv_up − setpoint_up) / setpoint_up)
q_coupling  = reactor.q   · G_TANK_REACTOR   · dev_tank     (extra reactant → reactor runs hotter)
p_coupling  = vessel.k_in · G_REACTOR_VESSEL · dev_reactor  (hotter product → vessel pressurises)
```

The design trick: at the baseline operating point every unit sits *at* its
setpoint, so every deviation — and therefore every coupling — is **zero**. The
whole plant holds a connected steady state with no trips. The couplings switch on
**only** when an attack pushes a unit off its setpoint, and then carry the
disturbance downstream (tank → reactor → vessel). Coupling is **forward only**:
attacking the vessel leaves the tank and reactor undisturbed (back-pressure is a
documented simplification, not an oversight).

---

## 3. Control and safety (the two PLCs)

### 3.1 Control PLC — BPCS

The BPCS runs a **proportional loop** holding each PV at a **writable setpoint**:

```
feed_cmd = clamp(bias + kp·(SP − PV), 0, 1000)        (per-mille valve command)
```

`kp` is signed (negative for the reactor, whose actuator removes energy). Added
real-SIF realism, all defined in `../common/scenarios.py` and compiled into the
Structured Text by `../plc/load_scenario.py`:

- an operator **setpoint clamp** `[SP_LO, SP_HI]` — so a naive "write an off-scale
  setpoint" attack is neutralised (the clamped SP settles the process *below* the
  trip);
- **AUTO / MANUAL** mode with bumpless transfer (`BPCS_MAN_MODE` / `BPCS_MAN_OUT`);
- an output **rate limit** (`CTRL_RATE_LIM_PM`).

### 3.2 Safety PLC — SIS

The SIS is an independent high-high **trip**: when `PV ≥ trip_hh` it forces the
protective action (force feed shut / emergency max cooling / open relief) and
latches. It also carries a pre-alarm "**H**", a **bad-PV fail-safe** trip, and a
**manual reset with deadband** (the latch only clears once PV has fallen a margin
below the trip). `SIS_OVERRIDE` is framed as a maintenance **BYPASS (MOS,
Maintenance Override Switch)** with its own `SIS_BYPASS_ACT` annunciation.

> **Lock-step invariant.** The dependency-free "fake PLC" path
> (`../bridge/plc_io.py` `FakeBpcs`/`FakeSis`) must stay byte-for-byte logically
> identical to the ST programs, so the demo behaves the same with or without real
> OpenPLC.

### 3.3 The hazard ladder (per scenario)

By convention `pv_full_scale == fail_at`, so the gauge "full" always means
"failure". Operating point, trip, and failure for each unit:

| Unit | PV | Setpoint | SIS trip (HH) | Failure | Protective action |
|------|----|----------|---------------|---------|-------------------|
| **T-101** | level | 5.0 m | 9.0 m | 10.0 m (OVERFLOW) | force feed shut |
| **R-301** | temperature | 80 °C | 110 °C | 300 °C (RUNAWAY) | emergency max cooling |
| **V-201** | pressure | 5.0 bar | 16.0 bar | 20.0 bar (RUPTURE) | open relief valve |

For T-101 and V-201 the clamped setpoint settles *below* the trip, so a
setpoint-tamper alone can never fail them — the attacker needs more. For R-301 the
exotherm reaches the trip even at the clamped setpoint, so a healthy SIS *catches*
the tamper (trips, no failure). The uniform invariant is **"a setpoint-tamper
never causes failure while the SIS is healthy."**

---

## 4. The attack, physically

The attack is external, over Modbus (`../attack/attack.py`). Because the setpoint
clamp defeats the naive off-scale-setpoint trick, the real driver is **MANUAL
mode**:

1. set `BPCS_MAN_MODE` and drive `BPCS_MAN_OUT` to the extreme → the control
   actuator is forced to its damaging position (feed wide open / coolant shut /
   compressor maxed), **bypassing** the clamp and the control law; **and**
2. set `SIS_OVERRIDE` (the maintenance bypass) → the high-high trip is suppressed
   so the independent protection layer no longer intervenes, **and** the
   operator's PV display **freezes green**.

The process now runs to failure. On the **HMI** (layer 3) everything looks
nominal and armed, because the HMI only sees what the (bypassed) PLCs report. On
the **3D view** (layer 4), driven straight from the physics, the targeted unit
climbs past its failure threshold — and the forward couplings carry the
disturbance into the downstream units. **That divergence is the demonstration.**

Contrast cases the script also supports: `--no-bypass` (manual drive but the SIS
left armed — defence-in-depth holds it safe) and `--setpoint-only` (the clamp
neutralises the naive attack). See [ATTACKS.md](ATTACKS.md).

---

## 5. Where the model is honest about its limits

- **Plausible, not validated** physics (hand-tuned ODEs, legible timescale).
- **Gas vessel, not a separator** — V-201 has only a pressure state; a real
  separator comes with the OpenModelica phase ([ROADMAP.md](ROADMAP.md)).
- **Forward-only coupling** — no back-pressure from downstream.
- **Attack pattern, not Triton's mechanism** — OpenPLC + Modbus, not
  Triconex + TriStation.
- **Modbus-only, single flowsheet, three hazard units** — everything heavier
  (OPC-UA, historian, segmentation, pcap, validated physics, a real separator) is
  a later, explicitly-scoped roadmap phase.

These are deliberate boundaries that keep the testbed demonstrable end-to-end
while staying truthful about what it does and does not reproduce.
