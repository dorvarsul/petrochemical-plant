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
  Runs the process normally: reads the sensor, drives the actuator (feed valve,
  compressor, coolant valve) with a proportional loop that holds the PV at a
  writable setpoint. **The attack manipulates that setpoint** to drive the process —
  but the BPCS is not a safety function, so this alone is caught by the SIS.

- **Safety PLC — SIS (Safety Instrumented System)**
  Independent of the BPCS. Holds the **trip/interlock** logic: when a measured
  variable crosses a safe limit, it commands the protective action (open relief,
  close feed, emergency cooling) regardless of what the BPCS wants.
  **The attack must also override this** to reach failure, and the override freezes
  the operator's HMI.

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

The attack is an external script (`attack/attack.py`) that reaches the plant only
over Modbus and acts on **both PLCs**:

1. **Manipulate the control setpoint (Control PLC — the driver)** — write an
   off-scale `BPCS_SETPOINT`, so the BPCS control loop drives its actuator to the
   limit (feed wide open / coolant shut) and pushes the true PV toward failure.
2. **Override the trip/interlock (Safety PLC)** — force the SIS trip output to its
   non-tripped state so the interlock never fires even as the true PV crosses the
   safe limit. (Without this, step 1 alone is caught by the healthy SIS.)
3. **Spoof the operator view (Safety PLC)** — the override also freezes the register
   the HMI polls (`SIS_PV_HMI`) at its last nominal value, so the HMI keeps showing a
   safe, steady process.

The physics model keeps integrating the *true* state the whole time, so the 3D view
diverges from the HMI and runs to failure. With no attacker, the baseline control
loop holds setpoint indefinitely. In Phase 0 this is done with **Modbus register
writes only** — it reproduces the attack *pattern* (manipulate the setpoint, defeat
the SIS, blind the operator), not the Triton *mechanism* (TriStation/Triconex
firmware injection).

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
                                       │ setpoint    │      │ trip/interlock  │
                                       │ control     │      │ logic           │
                                       └──────┬──────┘      └────────┬────────┘
                                         ▲ ATTACK ①            ▲ ATTACK ②③
                                         │ force setpoint      │ override trip +
                                         │ (drive actuator)    │ freeze HMI regs
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
  cannot write a slave's discrete-input / input-register tables.
- The two PLCs are **separate Modbus slaves** (two OpenPLC instances on different
  host ports). The bridge addresses a register by *(instance, table, address)*,
  not one shared namespace.
- **OpenPLC located-variable → Modbus mapping, and the direction trap.** OpenPLC
  maps `%QXn.m` → coil `n*8+m`, `%QWk` → holding register `k`, and `%MWk` → holding
  register `1024 + k`. The direction matters, and a `%QW` is **not** a usable
  sensor input: a `%QW` output word is rewritten from the PLC program's image every
  scan, so a Modbus master write to it is clobbered and **never reaches the
  program** (verified against the running v3 runtime — `VARIABLES.csv` classifies a
  `%QW` located var as `OUT`). So:
    - **Sensor (master → program):** a `%MW` **memory word** (`SIS_PV`/`BPCS_PV` at
      holding `1024`), which the master and the program share in place with no scan
      clobber. Coils are shared too, which is why the override coil reaches the
      program.
    - **Actuator / HMI mirror / trip (program → master):** `%QW` holding registers
      and `%QX` coils, read back by the bridge/HMI.

  (An earlier revision put the PV sensor on `%QW0`; that silently failed — the SIS
  never saw the true PV. The `%MW` mapping is the fix. A faithful input-register /
  discrete-input mapping is still a later-phase item.)
- Analog values are scaled to integers **per scenario** (`reg = round(pv ×
  reg_scale)`), the single source being `scenario.reg_scale` in
  `common/scenarios.py`: level in mm (`×1000`), pressure in cbar (`×100`),
  temperature in ddeg C (`×10`). Valve position is a scenario-independent per-mille
  (`0–1000`). Scale/units documented per row.

### Scenario semantics (all three share one shape)

The map encodes one control/attack story, parameterized per scenario — a
**modeling choice**, documented, not a claim of plant fidelity. The three
scenarios differ only in the PV, its scaling, the trip threshold, and the
physical meaning of the protective action:

> **Connected plant (Phase 1, see `ROADMAP.md`).** The register map below is the
> single-unit map: exactly one PV/BPCS/SIS set, as the standalone sims and the
> live OpenPLC stack use it. Phase 1 *also* runs all three units as **one
> flowsheet** — `T-101` feed tank → `R-301` reactor → `V-201` separation vessel,
> forward-coupled (tank overfill warms the reactor; reactor runaway pressurises
> the vessel) — with each scenario reframed as *which unit the attack targets*.
> That connected plant is implemented on the Python + in-memory-PLC path
> (`physics/plant.py`, `bridge/plant_bridge.py`), where each unit owns its own
> BPCS+SIS pair with an independent register space, so the identical addresses
> below never collide. Extending the **live** OpenPLC stack to three unit pairs
> (this map replicated per unit, plus per-unit ST programs) is Phase 2, alongside
> the whole-plant visualization. Validated OpenModelica physics is the **final**
> phase, behind the unchanged `model.step(dt, feed_cmd, trip)` interface.

| Scenario | PV (`reg_scale`) | BPCS actuator (control gain) | SIS trip (`trip_hh`) | Protective action | Failure |
|----------|------------------|------------------------------|----------------------|-------------------|---------|
| tank    | level, m (`×1000`)    | feed valve (+)       | 9.0 m (`9000`)   | force feed shut  | overflow at 10 m |
| vessel  | pressure, bar (`×100`)| compressor feed (+)  | 16.0 bar (`1600`)| open relief valve| rupture at 20 bar |
| reactor | temperature, °C (`×10`)| coolant valve (−)   | 110 °C (`1100`)  | emergency max cooling| runaway at 300 °C |

- **Baseline (no attacker):** the BPCS runs a proportional loop that holds the PV at
  its writable `BPCS_SETPOINT`, modulating the actuator —
  `feed_cmd = clamp(bias + kp·(SP − PV), 0, 1000)`. The gain `kp` is **signed**:
  positive where opening the actuator raises the PV (tank/vessel), negative for the
  reactor, whose coolant valve lowers it. The process simply sits at setpoint
  forever, and the SIS high-high trip (armed, watching the true PV) never fires. The
  operator HMI shows the PV and "SIS armed" status **sourced from the Safety PLC** —
  so a single SIS compromise both defeats protection and blinds the operator.
  (Concentrating the HMI's safety display on the SIS is a simplification.)
- **Protective action:** at PV ≥ `trip_hh`, the healthy SIS latches `SIS_TRIP_CMD`;
  the physics model applies it in its scenario-specific way and holds the process
  safe.
- **Attack (external, over Modbus — two writes):**
  1. **Control-setpoint manipulation (the driver):** the attacker writes an
     off-scale `BPCS_SETPOINT` to the Control PLC, so the BPCS control loop drives
     its actuator to the limit (feed valves wide open / the coolant valve shut),
     pushing the true PV toward failure. Alone, this is caught by a healthy SIS,
     which trips and holds the process safe (a defense-in-depth contrast).
  2. **Safety defeat + HMI spoof:** the attacker sets `SIS_OVERRIDE`, so the SIS
     holds `SIS_TRIP_CMD`/`SIS_TRIP_STATUS` deasserted and **stops refreshing**
     `SIS_PV_HMI` — the operator's PV freezes at its last nominal value while the
     true PV runs to failure. (The attacker does **not** spoof `BPCS_SETPOINT`
     itself, so a vigilant operator could notice the changed setpoint — one real
     tell against an otherwise green HMI.)

  This abuses a writable control setpoint plus a force/override path, both reachable
  over Modbus — the attack **pattern** (manipulate the setpoint, defeat the safety
  layer, blind the operator), not Triton's program-injection **mechanism** (Phase 4).
  See `OVERVIEW.md` scope limit. `attack/attack.py` is the standalone attacker;
  `--reset` stands it down.
- **How the commands reach the physics:** the bridge reads `SIS_TRIP_CMD` and
  `BPCS_FEED_CMD` and calls `model.step(dt, feed_cmd, trip)`; each model applies the
  actuator and the trip itself (tank: feed valve / feed shut; vessel: compressor /
  relief; reactor: coolant valve / emergency cooling). `trip_hh` and the control
  gains are program constants whose single source is `common/scenarios.py`, which
  `plc/load_scenario.py` substitutes into the SIS and BPCS programs at load time. The
  `BPCS_SETPOINT` register is seeded with the safe setpoint at startup/reset and
  never rewritten by the bridge, so an attacker's setpoint persists until reset.

### Register map (all scenarios)

Two slaves. Addresses below are the Modbus addresses the bridge uses; `ST var` is
the OpenPLC located variable the program declares. The map is identical for every
scenario — only the PV scaling (see the table above) changes. **The PV sensor is a
`%MW` memory word (holding `1024`), not `%QW`** — see the direction trap above.

**Control PLC — BPCS** · Modbus slave, default `localhost:5020`

| Table | Addr | ST var | Name            | Direction                  | Units / scale        | Meaning                                         |
|-------|------|--------|-----------------|----------------------------|----------------------|-------------------------------------------------|
| hold  | `1024`| `%MW0`| `BPCS_PV`       | physics → PLC (master wr.) | scaled PV            | true PV measured by BPCS; program reads it      |
| hold  | `1025`| `%MW1`| `BPCS_SETPOINT` | operator/**attack** → PLC (master wr.) | scaled PV | control setpoint; program reads it. Seeded safe at startup/reset; **attack writes off-scale.** A `%MW` word so a master write reaches the program. |
| hold  | `1`  | `%QW1` | `BPCS_FEED_CMD` | PLC → physics (master rd.) | per-mille (`0–1000`) | actuator position from the control loop `clamp(bias + kp·(SP−PV))` |

**Safety PLC — SIS** · Modbus slave, default `localhost:5021`

Coils (1-bit, master read/write):

| Addr | ST var   | Name              | Direction                  | Meaning                                                                        |
|------|----------|-------------------|----------------------------|--------------------------------------------------------------------------------|
| `0`  | `%QX0.0` | `SIS_TRIP_CMD`    | PLC → physics (master rd.) | 1 = trip (force protective action). Healthy SIS latches at PV ≥ `trip_hh`. **Attack holds 0.** |
| `1`  | `%QX0.1` | `SIS_TRIP_STATUS` | PLC → HMI (master rd.)      | 1 = tripped, 0 = armed-normal (operator indicator). **Attack holds 0.**        |
| `2`  | `%QX0.2` | `SIS_OVERRIDE`    | attack → PLC (master wr.)   | 0 normal; 1 = force/bypass. Program only reads it. **Attack sets 1.**          |

Holding registers (16-bit, master read/write):

| Addr   | ST var | Name          | Direction                  | Units / scale | Meaning                                                                      |
|--------|--------|---------------|----------------------------|---------------|------------------------------------------------------------------------------|
| `1024` | `%MW0` | `SIS_PV`      | physics → PLC (master wr.) | scaled PV     | true PV measured by SIS; program reads it (independent sensor)               |
| `1`    | `%QW1` | `SIS_PV_HMI`  | PLC → HMI (master rd.)     | scaled PV     | displayed PV = PV normally; under `SIS_OVERRIDE` the program stops updating it, so it **freezes** at its last nominal value |

> This map is authoritative and lives in code as `common/registers.py` — the bridge,
> both PLC programs, the HMI, and the attack script import names/addresses from there,
> never hard-coding per file. A per-scan heartbeat register (so a frozen HMI would be
> detectable) is deliberately **omitted** in Phase 0 and noted as a Phase-3 detection
> hook.
