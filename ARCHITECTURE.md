# PetroTwin — Architecture

This document describes the four layers, how they connect, the two-PLC split, the two
data taps, where the attack injects, and the Modbus register-map approach. It is a
software simulation testbed; see `OVERVIEW.md` for the honest scope limit.

## The four layers

| Layer       | Component (Phase 0)          | Role                                                     | Spoofable? |
|-------------|------------------------------|----------------------------------------------------------|------------|
| Physics     | Python ODE model             | Holds the **true** process state (ground truth)          | No         |
| PLC         | OpenPLC ×2 (Control + Safety)| Control logic (BPCS) and trip/interlock logic (SIS)      | Via network |
| HMI/SCADA   | FUXA (or similar)            | Operator view; polls PLCs over Modbus                    | **Yes**    |
| 3D view     | Three.js                     | Ground-truth visualization, driven direct from physics   | No         |

## The two-PLC split

The PLC layer is deliberately **two separate OpenPLC instances**, mirroring the
real-world separation of basic process control from safety:

- **Control PLC — BPCS (Basic Process Control System)**
  Runs the process normally: reads sensors, drives actuators (pump, feed valve,
  cooling), keeps the process at setpoint. Not the attack target.

- **Safety PLC — SIS (Safety Instrumented System)**
  Independent of the BPCS. Holds the **trip/interlock** logic: when a measured
  variable crosses a safe limit, it commands the protective action (open relief,
  close feed, open cooling) regardless of what the BPCS wants.
  **This is what the attack compromises.**

Keeping them separate is what makes the demonstration meaningful: defeating the SIS
is a distinct, targeted act, not a side effect of controlling the plant.

## The two data taps

Two independent read paths observe the process. This separation is the core of the
testbed:

1. **HMI ← PLC (via Modbus).**
   The HMI never talks to the physics model. It polls the PLCs' Modbus registers.
   Whatever the (compromised) Safety PLC chooses to report is what the operator sees.
   → **spoofable.**

2. **3D ← physics (direct).**
   The Three.js view reads the physics model's true state directly (shared
   state/IPC), bypassing the PLCs and the network entirely.
   → **unspoofable** by any network/PLC-layer attack.

> Rule: the 3D view must **never** be fed from the HMI or the PLC registers. If it
> ever is, the demonstration is invalid, because then the "ground truth" could be
> spoofed too.

## The Modbus bridge

A bridge process couples the Python physics model to the two OpenPLC instances over
Modbus each cycle:

- **Physics → PLC (sensors):** write true process variables into the PLCs' input
  registers (what the controllers "measure").
- **PLC → Physics (actuators):** read the PLCs' output coils/registers (valve/pump
  commands) back and apply them to the physics model.

This bridge, and the time-synchronization of the four independent processes across
it, is the **single biggest technical risk** in the whole project — more than any
individual layer. See `ROADMAP.md`.

## Where the attack injects

The attack is a script that acts on the **Safety PLC (SIS)** over Modbus:

1. **Override the trip/interlock** — force the SIS trip output to its non-tripped
   state (hold the protective actuator command inactive) so the interlock never
   fires even as the true process crosses the safe limit.
2. **Spoof the operator view** — hold the registers the HMI polls at nominal values
   (freeze/clamp), so the HMI keeps showing a safe, steady process.

The physics model keeps integrating the *true* state the whole time, so the 3D view
diverges from the HMI and runs to failure. In Phase 0 this is done with **Modbus
register writes only** — it reproduces the attack *pattern* (defeat the SIS, blind
the operator), not the Triton *mechanism* (TriStation/Triconex firmware injection).

## Data-flow diagram

```
                    ┌──────────────────────────────────────┐
                    │              PHYSICS                  │
                    │   Python ODE model (TRUE STATE)       │
                    │   P (pressure), L (level),            │
                    │   T (temp), C (concentration)         │
                    └───────┬───────────────────────┬───────┘
          direct, read-only │                       │  sensors ↓ / actuators ↑
          (SHARED STATE)    │                       │  (MODBUS BRIDGE — sync loop,
                            ▼                       │   biggest project risk)
                    ┌───────────────┐               │
                    │   3D VIEW     │               │
                    │   Three.js    │        ┌──────┴───────────────┐
                    │  GROUND TRUTH │        │                      │
                    │ (UNSPOOFABLE) │        ▼                      ▼
                    └───────────────┘  ┌─────────────┐      ┌─────────────────┐
                                       │ CONTROL PLC │      │  SAFETY PLC     │
                                       │   (BPCS)    │      │   (SIS)         │
                                       │  OpenPLC    │      │  OpenPLC        │
                                       │ control     │      │ trip/interlock  │
                                       │ logic       │      │ logic           │
                                       └──────┬──────┘      └────────┬────────┘
                                              │                      │
                                              │              ▲ ATTACK INJECTS HERE
                                              │              │ (Modbus writes:
                                              │              │  override trip +
                                              │              │  freeze HMI regs)
                                              │ Modbus poll   │ Modbus poll
                                              └───────┬───────┘
                                                      ▼
                                              ┌───────────────┐
                                              │   HMI/SCADA   │
                                              │     FUXA      │
                                              │  (SPOOFABLE)  │
                                              └───────────────┘

   Legend:  ──► data flow      PHYSICS→3D is DIRECT (no PLC, no network)
            HMI sees only what the PLCs report → attacker-controllable
```

## Modbus register-map approach

The bridge, the PLC logic, the HMI, and the attack script must all agree on one
register map. It is defined once **here** and referenced everywhere. Phase-0
conventions:

- Each OpenPLC instance is a **Modbus slave**; the bridge is the **Modbus master**.
  A master can read/write **coils** (bool) and **holding registers** (16-bit) but
  cannot write a slave's discrete-input / input-register tables. So Phase 0 carries
  everything in **coils + holding registers**: the bridge *writes* sensor values
  into holding registers the PLC program treats as read-only inputs, and *reads*
  back the coils/registers the program drives as outputs. (A more faithful
  input-register / discrete-input mapping is a Phase-2 item.)
- The two PLCs are **separate Modbus slaves** (two OpenPLC instances on different
  host ports), each with its **own address space starting at 0**. The bridge
  addresses a register by *(instance, table, address)*, not one shared namespace.
  Coils and holding registers are separate tables, so coil `0` and holding
  register `0` on the same instance are distinct.
- OpenPLC maps IEC 61131-3 located variables to Modbus as `%QWk` → holding
  register `k` and `%QXn.m` → coil `n*8+m`. A Modbus master may **write** coils and
  holding registers and **read** every table, so sensor values are injected into
  `%QW` holding registers that the program treats as inputs. (`%IW`/`%IX` are not
  master-writable; a faithful input mapping is a Phase-2 item.)
- Analog values are scaled to integers: **level in millimetres**
  (`mm = round(level_m × 1000)`, range `0–10000`) and **valve position in per-mille**
  (`0–1000`). Scale/units documented per row.

### Phase-0 scenario semantics (tank overfill)

The map encodes this control/attack story — a **Phase-0 modeling choice**, documented,
not a claim of plant fidelity:

- **Normal:** a fill is in progress. The BPCS holds the feed valve open; the SIS
  high-level trip is the automatic overflow protection, armed and watching the true
  level. The operator HMI shows the level and an "SIS armed" status **sourced from
  the Safety PLC** — so a single SIS compromise is enough to both defeat protection
  and blind the operator. (Concentrating the HMI's safety display on the SIS is a
  Phase-0 simplification; distributed display sourcing is a Phase-2 topic.)
- **Protective action:** at level ≥ high-high (`trip_hh`, 9 m), the healthy SIS
  asserts `SIS_TRIP_CMD`; the bridge applies it as feed-shut and the tank holds safe.
- **Attack (Safety PLC only):** the attacker sets `SIS_OVERRIDE`, after which the SIS
  logic holds `SIS_TRIP_CMD` and `SIS_TRIP_STATUS` deasserted (no trip, indicator
  "armed") and **stops refreshing** `SIS_LEVEL_HMI`, so the operator's level freezes at
  its last nominal value while the true level keeps rising. The physics runs to
  overflow while the HMI stays green. This abuses a force/override path reachable over
  Modbus — the attack **pattern**, not Triton's program-injection **mechanism**
  (Phase 3). See `OVERVIEW.md` scope limit.
- **Effective feed applied to the physics each bridge cycle:**
  `feed = 0 if SIS_TRIP_CMD else BPCS_FEED_CMD`, where a healthy SIS computes
  `SIS_TRIP_CMD = latch(level ≥ trip_hh)` unless `SIS_OVERRIDE` is set.
  `trip_hh` is a program constant in Phase 0 (a configurable setpoint register is a
  later-phase option, and a second attack variant).

### Register map — Phase 0 (tank overfill)

Two slaves, each addressed from 0. Addresses below are the Modbus addresses the
bridge uses; `ST var` is the OpenPLC located variable the program declares.

**Control PLC — BPCS** · Modbus slave, default `localhost:5020`

Holding registers (16-bit, master read/write):

| Addr | ST var | Name            | Direction                  | Units / scale        | Meaning                                         |
|------|--------|-----------------|----------------------------|----------------------|-------------------------------------------------|
| `0`  | `%QW0` | `BPCS_LEVEL_PV` | physics → PLC (master wr.) | mm (`0–10000`)       | true level measured by BPCS; program reads it   |
| `1`  | `%QW1` | `BPCS_FEED_CMD` | PLC → physics (master rd.) | per-mille (`0–1000`) | fill-valve position; Phase-0 program holds `1000` (open) |

**Safety PLC — SIS** · Modbus slave, default `localhost:5021`

Coils (1-bit, master read/write):

| Addr | ST var   | Name              | Direction                  | Meaning                                                                        |
|------|----------|-------------------|----------------------------|--------------------------------------------------------------------------------|
| `0`  | `%QX0.0` | `SIS_TRIP_CMD`    | PLC → physics (master rd.) | 1 = trip (force feed shut). Healthy SIS latches at level ≥ `trip_hh`. **Attack holds 0.** |
| `1`  | `%QX0.1` | `SIS_TRIP_STATUS` | PLC → HMI (master rd.)      | 1 = tripped, 0 = armed-normal (operator indicator). **Attack holds 0.**        |
| `2`  | `%QX0.2` | `SIS_OVERRIDE`    | attack → PLC (master wr.)   | 0 normal; 1 = force/bypass. Program only reads it. **Attack sets 1.**          |

Holding registers (16-bit, master read/write):

| Addr | ST var | Name            | Direction                  | Units / scale  | Meaning                                                                      |
|------|--------|-----------------|----------------------------|----------------|------------------------------------------------------------------------------|
| `0`  | `%QW0` | `SIS_LEVEL_PV`  | physics → PLC (master wr.) | mm (`0–10000`) | true level measured by SIS; program reads it (independent sensor)            |
| `1`  | `%QW1` | `SIS_LEVEL_HMI` | PLC → HMI (master rd.)     | mm (`0–10000`) | displayed level = PV normally; under `SIS_OVERRIDE` the program stops updating it, so it **freezes** at its last nominal value |

> This map is authoritative and lives in code as `common/registers.py` — the bridge,
> both PLC programs, the HMI, and the attack script import names/addresses from there,
> never hard-coding per file. A per-scan heartbeat register (so a frozen HMI would be
> detectable) is deliberately **omitted** in Phase 0 and noted as a Phase-2 detection
> hook.
