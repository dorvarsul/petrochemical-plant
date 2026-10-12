# PLC layer — two OpenPLC v3 instances (Phase 0)

The PLC layer is **two independent OpenPLC v3 soft-PLCs**, each a Modbus TCP
slave, matching the documented stack (IEC 61131-3 Structured Text + Modbus):

| Instance | Role                         | ST program        | Web UI                  | Modbus slave      |
|----------|------------------------------|-------------------|-------------------------|-------------------|
| `bpcs`   | Control PLC (BPCS)           | `bpcs/bpcs.st`    | http://localhost:8081   | `localhost:5020`  |
| `sis`    | Safety PLC (SIS) — attack target | `sis/sis.st`  | http://localhost:8082   | `localhost:5021`  |

The Modbus ports match `ENDPOINTS` in `common/registers.py`; the ST programs'
`%QW`/`%QX` located variables match the register map in `ARCHITECTURE.md`.

> OpenPLC v3 is end-of-life (superseded by OpenPLC Runtime v4). It is used here
> because it faithfully provides the documented stack. Moving to v4 (or to the
> mechanism-level work) is a later-phase decision.

## 1. Build the image (once)

Built from pinned official source — no third-party image is pulled:

```bash
cd plc
docker compose build          # clones OpenPLC_v3 @ b5d4135 and builds petrotwin/openplc:v3
```

## 2. Start both PLCs

```bash
docker compose up -d
docker compose ps             # both containers should be "running"
```

## 3. Load + start each program (one-time, via the web UI)

OpenPLC v3 loads and compiles ST programs through its web interface; there is no
reliable headless path, so this is a manual one-time step per instance. Default
login is **`openplc` / `openplc`**.

For **BPCS** (http://localhost:8081) load `bpcs/bpcs.st`, and for
**SIS** (http://localhost:8082) load `sis/sis.st`. In each UI:

1. **Programs → Upload Program** → choose the `.st` file → **Upload**.
2. On the program page, click **Compile** → wait for "Compilation finished successfully".
3. **Settings** → enable **Modbus Server** (Enabled), leave port **502** → **Save**.
4. **Dashboard → Start PLC**. The status should read "running".

> Load the **right** program into the **right** instance — `bpcs.st` into `bpcs`
> (8081), `sis.st` into `sis` (8082). They are not interchangeable.

## 4. Verify the Modbus slaves are listening

```bash
# from the repo root
python3 - <<'PY'
import socket
for name, port in (("bpcs", 5020), ("sis", 5021)):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port)); print(f"{name}: {port} OPEN")
    except OSError as e:
        print(f"{name}: {port} closed ({e})")
    finally:
        s.close()
PY
```

Functional round-trip (writing a sensor into `%QW0` and reading back the PLC's
outputs) is exercised by the Modbus bridge in Phase-0 step 4, with tests per
`TESTING.md` section 3.

## Modbus mapping (recap)

Authoritative map: `common/registers.py` / `ARCHITECTURE.md`. OpenPLC maps
`%QWk` → holding register `k`, `%MWk` → holding register `1024 + k` (a shared
**memory** word), and `%QXn.m` → coil `n*8+m`. A master write to a `%QW` output is
clobbered by the program image each scan, so every master-writable input (sensor,
setpoint, manual output, mode, reset, valve commands) is a `%MW` word or a
program-read coil; program outputs are `%QW`/`%QX`.

- **BPCS** holding: `%MW0` `BPCS_PV` (sensor in), `%MW1` `BPCS_SETPOINT`
  (operator/attack in, clamped), `%MW2` `BPCS_MAN_OUT` (manual cmd / AUTO track),
  `%QW1` `BPCS_FEED_CMD` (out). BPCS coils: `0` `BPCS_MAN_MODE` (in),
  `1`–`3` PV_HI/PV_LO/SP_CLAMPED (out), `4` `BPCS_OPV_CMD` (export valve in),
  `5` `BPCS_OPV_OUT` (out).
- **SIS** coils: `0` `SIS_TRIP_CMD` (out), `1` `SIS_TRIP_STATUS` (out),
  `2` `SIS_OVERRIDE` (maintenance bypass, attacker in), `3` `SIS_RESET` (in),
  `4`–`5` PREALARM_H/PV_BAD (out), `6` `SIS_BLK_OPEN_CMD` (fill valve in),
  `7` `SIS_BLK_OUT` (out), `8`=`%QX1.0` `SIS_BYPASS_ACT` (out). SIS holding:
  `%MW0` `SIS_PV` (sensor in), `%QW1` `SIS_PV_HMI` (out, frozen under bypass).

The loader substitutes the per-scenario constants into both programs: BPCS
`BIAS_PM`/`KP_NUM`/`KP_DEN` + the clamp `SP_LO`/`SP_HI`; SIS `TRIP_HH` +
`PRE_ALARM_H`/`RESET_DB`/`PV_MAX` (`RATE_LIM`/`PV_MIN` are fixed).

### Connected plant: one program, three units per slave

The addresses above are the **single-unit block** (unit 0). The connected
whole-plant view runs all three units on these same two slaves — one BPCS for all
three units' control, one SIS for all three units' safety — by giving each unit
its own **offset block**: `registers.unit_addr` adds a per-unit stride
(`COIL_STRIDE=16`, `MW_STRIDE=16`, `QW_STRIDE=8`; unit 0 = tank unchanged,
reactor +1, vessel +2). `plc.load_scenario.load_plant` generates one ST program
per slave with **three POU instances** (`prog_tank`/`prog_reactor`/`prog_vessel`
on one 100 ms task) at the matching located addresses — e.g. BPCS `pv` at
`%MW0`/`%MW16`/`%MW32`, `man_mode` at `%QX0.0`/`%QX2.0`/`%QX4.0`. Load it with:

```bash
python3 -m plc.load_scenario --plant     # all three units into both PLCs
```

`ui/plant_server.py --live` (and `run_plant.sh start`) call this on startup, then
drive the units over Modbus through `bridge.plc_io.ModbusUnitView`.

## Stop / reset

```bash
docker compose down           # stop and remove containers
```

Uploaded programs and settings persist in each container's `/docker_persistent`
volume; after `down` + `up` you may need to **Start PLC** again from the UI.
