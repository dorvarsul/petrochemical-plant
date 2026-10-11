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
- Values are grouped by owner: a **Control PLC (BPCS)** block (addresses `0–9`) and
  a **Safety PLC (SIS)** block (addresses `10–19`), on separate slave units so the
  two PLCs stay independent. Coils and holding registers are separate address
  spaces, so a coil `10` and a holding register `10` are distinct.
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
- **Attack (Safety PLC only):** the attacker sets `SIS_OVERRIDE`, which makes the SIS
  logic (a) hold `SIS_TRIP_CMD` deasserted (feed never shuts) and (b) pass
  attacker-held values to `SIS_TRIP_STATUS` ("armed") and `SIS_LEVEL_HMI` (frozen near
  setpoint). The physics rises to overflow while the HMI stays green. This abuses a
  force/override path reachable over Modbus — the attack **pattern**, not Triton's
  program-injection **mechanism** (Phase 3). See `OVERVIEW.md` scope limit.
- **Effective feed applied to the physics each bridge cycle:**
  `feed = 0 if SIS_TRIP_CMD else BPCS_FEED_CMD`, where a healthy SIS computes
  `SIS_TRIP_CMD = latch(level ≥ trip_hh)` unless `SIS_OVERRIDE` is set.
  `trip_hh` is a program constant in Phase 0 (a configurable setpoint register is a
  later-phase option, and a second attack variant).

### Register map — Phase 0 (tank overfill)

**Coils** (1-bit, master read/write)

| Addr | Name              | Owner       | Direction            | Meaning                                                                 |
|------|-------------------|-------------|----------------------|-------------------------------------------------------------------------|
| `10` | `SIS_TRIP_CMD`    | Safety/SIS  | PLC → physics (bridge)| 1 = trip (force feed shut). Healthy SIS sets at level ≥ `trip_hh`, latched. **Attack holds 0.** |
| `11` | `SIS_TRIP_STATUS` | Safety/SIS  | PLC → HMI            | 1 = tripped/shutdown, 0 = armed-normal (operator indicator). **Attack holds 0.** |
| `12` | `SIS_OVERRIDE`    | Safety/SIS  | attack → PLC         | 0 normal; 1 = force/bypass (suppress trip + pass attacker HMI values). **Attack sets 1.** |

**Holding registers** (16-bit, master read/write)

| Addr | Name            | Owner       | Direction         | Units / scale        | Meaning                                                             |
|------|-----------------|-------------|-------------------|----------------------|--------------------------------------------------------------------|
| `0`  | `BPCS_LEVEL_PV` | Control/BPCS| physics → PLC     | mm (`0–10000`)       | true level measured by the BPCS (bridge writes)                    |
| `1`  | `BPCS_FEED_CMD` | Control/BPCS| PLC → physics     | per-mille (`0–1000`) | fill-valve position; Phase-0 holds `1000` (open)                   |
| `10` | `SIS_LEVEL_PV`  | Safety/SIS  | physics → PLC     | mm (`0–10000`)       | true level measured by the SIS (bridge writes; independent sensor) |
| `11` | `SIS_LEVEL_HMI` | Safety/SIS  | PLC → HMI         | mm (`0–10000`)       | level shown to operator; equals PV normally. **Attack freezes near `5000`.** |

> This table is authoritative. The bridge, both PLC programs, the HMI, and the attack
> script must import these names/addresses from one shared definition (e.g. a single
> `registers` module) — never hard-code addresses per file. A per-scan heartbeat
> register (so a frozen HMI is detectable) is deliberately **omitted** in Phase 0 and
> noted as a Phase-2 detection hook.
