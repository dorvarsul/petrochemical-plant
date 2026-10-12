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
  writable, **clamped** setpoint, with AUTO/MANUAL mode and output rate-limiting.
  **The attack flips it to MANUAL and drives the actuator directly** (the setpoint
  clamp defeats the older off-scale-setpoint trick) — but the BPCS is not a safety
  function, so this alone is caught by the SIS.

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

1. **Seize the actuator (Control PLC — the driver)** — write `BPCS_MAN_MODE = 1`
   and drive `BPCS_MAN_OUT` to the extreme (feed wide open / coolant shut), so the
   actuator pushes the true PV toward failure. This bypasses the control law and the
   setpoint clamp. (A naive off-scale `BPCS_SETPOINT` write is now clamped — caught —
   which is why the driver moved to MANUAL mode.)
2. **Bypass the trip/interlock (Safety PLC)** — set `SIS_OVERRIDE` (the maintenance
   bypass) so the SIS trip output stays non-tripped even as the true PV crosses the
   safe limit. (Without this, step 1 alone is caught by the healthy SIS.)
3. **Spoof the operator view (Safety PLC)** — the bypass also freezes the register
   the HMI polls (`SIS_PV_HMI`) at its last nominal value, so the HMI keeps showing a
   safe, steady process (though `SIS_BYPASS_ACT` annunciates the bypass).

The physics model keeps integrating the *true* state the whole time, so the 3D view
diverges from the HMI and runs to failure. With no attacker, the baseline control
loop holds setpoint indefinitely. In Phase 0 this is done with **Modbus register
writes only** — it reproduces the attack *pattern* (seize the actuator, defeat the
SIS, blind the operator), not the Triton *mechanism* (TriStation/Triconex firmware
injection).

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

## Deployment topology

### Phase 0–2 — single host

Through Phase 2 every layer runs as a co-operating process on **one machine**: the
two OpenPLC instances are Modbus slaves on `localhost` ports 5020/5021, the bridge
and HMI are masters on the same host, and the 3D view reads the physics model via
in-process shared state/IPC. The diagram above is this single-host layout.

### Phase 2.5 — distributed four-PC over an L2 segment (planned)

The same software splits across **four physical Ubuntu Linux PCs on one layer-2
Ethernet segment** — one PC per layer. This is a deployment change only: the four
layers, the two-PLC split, the two data taps, the register map and the attack are
all unchanged.

| PC  | Layer hosted                 | Role on the wire                                  |
|-----|------------------------------|---------------------------------------------------|
| PC1 | **Physics + 3D view**        | Modbus **TCP master** (the bridge); 3D reads physics **locally** |
| PC2 | **Control PLC (BPCS)**       | Modbus **TCP slave**                              |
| PC3 | **Safety PLC (SIS)**         | Modbus **TCP slave**                              |
| PC4 | **HMI**                      | Modbus **TCP master** (polls PC2 + PC3)           |

```
   PC1  Physics + 3D (ground truth; 3D tap is LOCAL shared state)
        └─ bridge: Modbus TCP master ─┐
                                      ├─► PC2  Control PLC / BPCS (slave)
                                      └─► PC3  Safety PLC  / SIS  (slave)
   PC4  HMI: Modbus TCP master ───────────► PC2, PC3  (poll only)

                all four on ONE L2 Ethernet switch (single broadcast domain)
```

Key points, all preserving the invariants above:

- **Physics and 3D stay co-located on PC1.** The unspoofable data tap depends on
  the 3D view reading physics **directly**. Co-locating them keeps that read
  local; it must **never** be served across the L2, or the ground truth becomes
  spoofable. This is why the 3D view does not get its own PC.
- **`localhost` → real IPs.** The bridge (PC1) and HMI (PC4) address the PLCs by
  host IP and port over **Modbus TCP** instead of `localhost:5020/5021`. The only
  new configuration is a **host/IP map** (layer → address); the register map and
  all addresses inside it are unchanged.
- **The attacker is a real on-segment host.** It reaches the PLCs over Modbus TCP
  from a machine on the same L2 — a faithful on-network adversary.
- **L2 only (no segmentation yet).** One broadcast domain, no Purdue-level
  segmentation, OPC-UA, historian or pcap capture — those remain Phase 3.
- **Amplified risk: cross-machine time-sync.** The bridge/PLC/HMI sync loop now
  crosses a real network (link latency/jitter, independent clocks). Mitigate with
  a common time base (NTP, PTP if needed) and poll periods above the observed
  round-trip. See `ROADMAP.md`.

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

> **Connected plant (Phase 1–2, see `ROADMAP.md`).** The register map below is the
> **single-unit block**: exactly one PV/BPCS/SIS set, as the standalone sims use
> it. The connected plant runs all three units as **one flowsheet** — `T-101`
> feed tank → `R-301` reactor → `V-201` separation vessel, forward-coupled (tank
> overfill warms the reactor; reactor runaway pressurises the vessel) — with each
> scenario reframed as *which unit the attack targets*.
>
> On the fake-PLC path (`physics/plant.py`, `bridge/plant_bridge.py`) each unit
> owns its own in-memory BPCS+SIS with an independent register space, so the
> identical single-unit addresses below never collide. On the **live** path the
> same three units run on just **two** OpenPLC slaves — one BPCS carries control
> for all three, one SIS carries safety — by giving each unit its own **offset
> block** within a slave: `registers.unit_addr(table, base_addr, unit_index)`
> adds a per-unit stride (unit 0 = tank = the base addresses unchanged; see the
> *Per-unit offset blocks* table below). `bridge.plc_io.ModbusUnitView` applies
> the offset so `PlantBridge` passes the bare single-unit addresses, and
> `plc/load_scenario.py` generates one 3-instance ST program per slave at the
> matching located addresses. Validated OpenModelica physics is the **final**
> phase, behind the unchanged `model.step(dt, feed_cmd, trip)` interface.

| Scenario | PV (`reg_scale`) | BPCS actuator (control gain) | SIS trip (`trip_hh`) | Protective action | Failure |
|----------|------------------|------------------------------|----------------------|-------------------|---------|
| tank    | level, m (`×1000`)    | feed valve (+)       | 9.0 m (`9000`)   | force feed shut  | overflow at 10 m |
| vessel  | pressure, bar (`×100`)| compressor feed (+)  | 16.0 bar (`1600`)| open relief valve| rupture at 20 bar |
| reactor | temperature, °C (`×10`)| coolant valve (−)   | 110 °C (`1100`)  | emergency max cooling| runaway at 300 °C |

- **Baseline (no attacker):** the BPCS runs a proportional loop that holds the PV at
  its writable `BPCS_SETPOINT`, modulating the actuator —
  `feed_cmd = clamp(bias + kp·(SP − PV), 0, 1000)` — now with real-SIF trimmings:
  the operator setpoint is **clamped** to `[SP_LO, SP_HI]`, the loop runs in
  **AUTO/MANUAL** with bumpless transfer, and the output is **rate-limited**. The
  gain `kp` is **signed**: positive where opening the actuator raises the PV
  (tank/vessel), negative for the reactor's coolant valve. The SIS high-high trip
  (armed, watching the true PV) never fires at baseline, and additionally
  annunciates a pre-alarm "H", a bad-PV fault, and a maintenance-bypass state. The
  operator HMI shows the PV and SIS status **sourced from the Safety PLC** — so a
  single SIS compromise both defeats protection and blinds the operator.
- **Protective action:** at PV ≥ `TRIP_HH` (or a bad PV), the healthy SIS latches
  `SIS_TRIP_CMD`; the physics model applies it in its scenario-specific way and
  holds the process safe. The latch clears only on the `SIS_RESET` pushbutton once
  PV has fallen a deadband below the trip.
- **Attack (external, over Modbus — the evolved path):** because the setpoint clamp
  now neutralises the old off-scale-setpoint trick, the attacker seizes the
  actuator through **MANUAL mode** instead:
  1. **Actuator seizure (the driver):** write `BPCS_MAN_MODE = 1` and drive
     `BPCS_MAN_OUT` to the dangerous extreme (feed valve wide open / coolant valve
     shut). This bypasses the control law *and* the clamp, pushing the true PV
     toward failure. Alone, this is caught by a healthy SIS, which trips and holds
     the process safe (the defense-in-depth contrast — `attack.py --no-bypass`).
  2. **Safety defeat + HMI spoof:** set `SIS_OVERRIDE` (the maintenance BYPASS), so
     the SIS holds `SIS_TRIP_CMD`/`SIS_TRIP_STATUS` deasserted and **stops
     refreshing** `SIS_PV_HMI` — the operator's PV freezes at its last nominal value
     while the true PV runs to failure. `SIS_BYPASS_ACT` annunciates the bypass —
     the one honest tell against an otherwise green HMI.

  (A naive **setpoint tamper** alone — `attack.py --setpoint-only` — is retained as
  a contrast: the BPCS clamps it and raises `BPCS_SP_CLAMPED`, so the process holds
  at the clamp, below trip, and never fails.) This abuses writable mode/output plus a
  force/bypass path, all reachable over Modbus — the attack **pattern** (seize the
  actuator, defeat the safety layer, blind the operator), not Triton's
  program-injection **mechanism** (Phase 4). See `OVERVIEW.md` scope limit.
  `attack/attack.py` is the standalone attacker; `--reset` stands it down (and
  pulses `SIS_RESET`).
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

Holding registers:

| Table | Addr | ST var | Name            | Direction                  | Units / scale        | Meaning                                         |
|-------|------|--------|-----------------|----------------------------|----------------------|-------------------------------------------------|
| hold  | `1024`| `%MW0`| `BPCS_PV`       | physics → PLC (master wr.) | scaled PV            | true PV measured by BPCS; program reads it      |
| hold  | `1025`| `%MW1`| `BPCS_SETPOINT` | operator/**attack** → PLC (master wr.) | scaled PV | control setpoint; program reads it. Seeded safe at startup/reset. **Clamped to `[SP_LO, SP_HI]`**, so an off-scale write can no longer saturate the actuator. A `%MW` word so a master write reaches the program. |
| hold  | `1026`| `%MW2`| `BPCS_MAN_OUT`  | operator/**attack** ↔ PLC (master wr.) | per-mille (`0–1000`) | manual actuator command. In AUTO the program **tracks** its own output here (bumpless transfer); in MANUAL the program uses it as the actuator position. **The evolved attack drives this directly** (bypasses the clamp). A `%MW` word. |
| hold  | `1`  | `%QW1` | `BPCS_FEED_CMD` | PLC → physics (master rd.) | per-mille (`0–1000`) | actuator position: AUTO control loop `clamp(bias + kp·(SP−PV))` or the manual command, then an output **rate limit** (≤ `RATE_LIM`/scan) |

Coils (1-bit):

| Addr | ST var   | Name            | Direction                  | Meaning                                                                        |
|------|----------|-----------------|----------------------------|--------------------------------------------------------------------------------|
| `0`  | `%QX0.0` | `BPCS_MAN_MODE` | operator/**attack** → PLC  | 0 = AUTO, 1 = MANUAL. Program reads it. **The evolved attack sets 1** to seize the actuator. |
| `1`  | `%QX0.1` | `BPCS_PV_HI_ALM`| PLC → HMI (master rd.)     | process alarm: PV ≥ `SP_HI`                                                    |
| `2`  | `%QX0.2` | `BPCS_PV_LO_ALM`| PLC → HMI (master rd.)     | process alarm: PV ≤ `SP_LO`                                                    |
| `3`  | `%QX0.3` | `BPCS_SP_CLAMPED`| PLC → HMI (master rd.)    | 1 = the operator setpoint was out of `[SP_LO, SP_HI]` and was clamped          |
| `4`  | `%QX0.4` | `BPCS_OPV_CMD`  | operator → PLC (master wr.)| product-EXPORT-valve open request (P-401 outlet). Program reads it.            |
| `5`  | `%QX0.5` | `BPCS_OPV_OUT`  | PLC → physics/HMI          | export-valve state (= `OPV_CMD`); the bridge applies it to P-401               |

**Safety PLC — SIS** · Modbus slave, default `localhost:5021`

Coils (1-bit, master read/write):

| Addr | ST var   | Name              | Direction                  | Meaning                                                                        |
|------|----------|-------------------|----------------------------|--------------------------------------------------------------------------------|
| `0`  | `%QX0.0` | `SIS_TRIP_CMD`    | PLC → physics (master rd.) | 1 = trip (force protective action). Healthy SIS latches at PV ≥ `TRIP_HH` (or a bad PV). **Attack holds 0 via bypass.** |
| `1`  | `%QX0.1` | `SIS_TRIP_STATUS` | PLC → HMI (master rd.)      | 1 = tripped, 0 = armed-normal (operator indicator). **Attack holds 0.**        |
| `2`  | `%QX0.2` | `SIS_OVERRIDE`    | attack → PLC (master wr.)   | maintenance **BYPASS (MOS)**; 0 normal, 1 = suppress trip + freeze HMI. Program only reads it. **Attack sets 1.** |
| `3`  | `%QX0.3` | `SIS_RESET`       | operator → PLC (master wr.) | manual reset pushbutton. Program reads it; clears the latch only when PV `< TRIP_HH − RESET_DB`. |
| `4`  | `%QX0.4` | `SIS_PREALARM_H`  | PLC → HMI (master rd.)      | 1 = PV ≥ `PRE_ALARM_H` (the "H" pre-alarm, below the "HH" trip)                |
| `5`  | `%QX0.5` | `SIS_PV_BAD`      | PLC → HMI (master rd.)      | 1 = transmitter out of `[PV_MIN, PV_MAX]`; forces a fail-safe trip             |
| `6`  | `%QX0.6` | `SIS_BLK_OPEN_CMD`| operator → PLC (master wr.) | block/FILL-valve open request (T-101 inlet). Program reads it.                 |
| `7`  | `%QX0.7` | `SIS_BLK_OUT`     | PLC → physics/HMI           | block-valve state = `BLK_OPEN_CMD AND NOT TRIP_CMD`; the trip physically shuts it (final element) |
| `8`  | `%QX1.0` | `SIS_BYPASS_ACT`  | PLC → HMI (master rd.)      | 1 = bypass active (MOS annunciation — the one honest "tell" during the attack). First coil in byte 1. |

Holding registers (16-bit, master read/write):

| Addr   | ST var | Name          | Direction                  | Units / scale | Meaning                                                                      |
|--------|--------|---------------|----------------------------|---------------|------------------------------------------------------------------------------|
| `1024` | `%MW0` | `SIS_PV`      | physics → PLC (master wr.) | scaled PV     | true PV measured by SIS; program reads it (independent sensor)               |
| `1`    | `%QW1` | `SIS_PV_HMI`  | PLC → HMI (master rd.)     | scaled PV     | displayed PV = PV normally; under `SIS_OVERRIDE` the program stops updating it, so it **freezes** at its last nominal value |

> **Terminal valves + P-401.** The FILL valve (T-101 inlet) is the tank SIS's
> block-valve final element (`SIS_BLK_OUT`): the operator opens it, the trip shuts
> it. The product EXPORT valve (P-401 outlet) is a plain BPCS operator valve
> (`BPCS_OPV_OUT`, no interlock). Both ride on the shared ST programs as generic
> I/O and only matter on the tank / vessel instances respectively. P-401 itself is
> a terminal **sink** (a level ODE in `physics/product.py`, fed by steady V-201
> bottoms, drawn by EXPORT) with LL/LO/HI/HH alarms — not an attack target, so it
> has no BPCS/SIS pair.

#### Per-unit offset blocks (connected-plant live path)

The table above is the **single-unit block** (unit 0). The connected plant places
three units on two slaves by adding a per-unit offset via
`registers.unit_addr(table, base_addr, unit_index)`; unit 0 (tank) is unchanged.
Strides: `COIL_STRIDE=16`, `MW_STRIDE=16` (`%MW`/holding ≥ 1024 band),
`QW_STRIDE=8` (`%QW` output band). The generated ST programs declare each unit's
located variables at exactly these addresses, and `ModbusUnitView` reads/writes
them for the bridge.

| Unit (`unit_index`) | BPCS/SIS coils | `%MW` (PV/SETPOINT/MAN_OUT · SIS PV) | `%QW` (FEED_CMD · PV_HMI) |
|---------------------|----------------|--------------------------------------|---------------------------|
| tank (0)            | `%QX0.*` (SIS `%QX0–1`) | `%MW0–2`  | `%QW1`  |
| reactor (1)         | `%QX2.*` (SIS `%QX2–3`) | `%MW16–18`| `%QW9`  |
| vessel (2)          | `%QX4.*` (SIS `%QX4–5`) | `%MW32–34`| `%QW17` |

All three units' registers stay distinct within each slave (no collisions), each
unit's writable input coils stay in its block's byte 0, and the whole span fits
OpenPLC v3's default Modbus image (coils ≤ 40, holding ≤ 1058).

> This map is authoritative and lives in code as `common/registers.py` — the bridge,
> both PLC programs, the HMI, and the attack script import names/addresses from there,
> never hard-coding per file. A per-scan heartbeat register (so a frozen HMI would be
> detectable) is deliberately **omitted** in Phase 0 and noted as a Phase-3 detection
> hook.
