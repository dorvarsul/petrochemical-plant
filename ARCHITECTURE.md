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
register map. It is defined once and referenced everywhere. Conventions:

- Group by owner: **Control PLC (BPCS)** block and **Safety PLC (SIS)** block.
- **Input registers / discrete inputs** = values the physics writes *into* the PLC
  (sensors, read-only to the PLC logic).
- **Holding registers / coils** = values the PLC logic writes *out* (actuator
  commands, setpoints, trip state) that the bridge and HMI read.
- Floats scaled to integers where needed (document scale/units per row).

### Register map (placeholder — fill in before Phase-0 coding)

| Reg name          | Modbus type        | Address | Owner       | Direction        | Units / scale | Meaning                               |
|-------------------|--------------------|---------|-------------|------------------|---------------|---------------------------------------|
| `TODO_sensor_1`   | input register     | `TODO`  | Control/BPCS| physics → PLC    | `TODO`        | e.g. process variable (P / L / T)     |
| `TODO_sensor_2`   | input register     | `TODO`  | Safety/SIS  | physics → PLC    | `TODO`        | safety-measured variable              |
| `TODO_actuator_1` | coil               | `TODO`  | Control/BPCS| PLC → physics    | bool          | e.g. feed valve / pump command        |
| `TODO_trip_cmd`   | coil               | `TODO`  | Safety/SIS  | PLC → physics    | bool          | protective actuator (relief/feed/cool)|
| `TODO_trip_state` | discrete input     | `TODO`  | Safety/SIS  | PLC → HMI        | bool          | SIS tripped / armed (HMI reads this)  |
| `TODO_hmi_pv`     | holding register   | `TODO`  | Safety/SIS  | PLC → HMI        | `TODO`        | value shown on operator screen        |
| `...`             | `...`              | `...`   | `...`       | `...`            | `...`         | `...`                                 |

> Keep this table authoritative. The attack script's targets (override `*_trip_cmd`
> / `*_trip_state`, freeze `*_hmi_pv`) must be expressed as rows here, not hard-coded
> addresses scattered across files.
