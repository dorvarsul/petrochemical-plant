# PetroTwin

A four-layer OT cybersecurity testbed that simulates a Triton/Trisis-style
attack end to end **in software**: compromise the safety layer, defeat the
trip/interlock, spoof the operator's HMI, and drive a simulated industrial
process to physical failure — for defensive research and training.

> **The operator's screen can lie. The physics cannot.** The HMI reads the PLCs
> over Modbus (spoofable); the 3D view reads the physics directly (unspoofable).
> That divergence is the whole point.

Start with **[OVERVIEW.md](OVERVIEW.md)**, then **[ARCHITECTURE.md](ARCHITECTURE.md)**,
**[ROADMAP.md](ROADMAP.md)**, **[TESTING.md](TESTING.md)**. Working agreement for
future sessions: **[CLAUDE.md](CLAUDE.md)**.

This is the Triton attack **pattern**, not its **mechanism** (OpenPLC ≠ Triconex;
Modbus override, not a TriStation exploit). See the scope limit in `OVERVIEW.md`.

## Status: **all three scenarios, working end to end (live)**

Three scenarios share one control/attack shape and are selectable everywhere:

| id | scenario | PV | SIS trip | failure |
|----|----------|----|----------|---------|
| `tank` | tank overfill | level (m) | 9 m | overflow at 10 m |
| `vessel` | vessel overpressure | pressure (bar) | 16 bar | rupture at 20 bar |
| `reactor` | reactor runaway | temperature (°C) | 110 °C | runaway at 300 °C |

Each runs the full divergence both offline (fake PLCs) and live (two real OpenPLC v3
instances over Modbus). Physics is pure-Python ODEs tuned for a legible demo, not
plant fidelity (OpenModelica realism is a later phase).

## Quick start

### Offline — no Docker (pure stdlib)
```bash
python3 -m unittest discover -s tests          # 145 tests (5 live-Modbus skipped)
python3 -m bridge.run_fake_demo                # tank (default)
python3 -m bridge.run_fake_demo vessel         # or vessel / reactor
python3 -m bridge.run_fake_demo reactor        # normal -> attack -> failure, HMI frozen
```

**Connected plant (Phase 1–2), no Docker.** The three units wired into one
flowsheet (T-101 → R-301 → V-201, forward-coupled), on the fake-PLC path:
```bash
python3 -m bridge.run_plant_demo reactor       # terminal: attack one unit, watch the plant respond
python3 -m ui.plant_server                      # whole-plant view (fake PLCs): http://127.0.0.1:8092
```
The whole-plant view shows a spoofable per-unit HMI strip over one unspoofable
3D ground-truth train; attack any unit and watch it run to failure while its HMI
tile stays green and the forward couplings carry the disturbance downstream.

### Live — the whole connected plant on real OpenPLC (one command)
`run_plant.sh` is the one-command runner: it brings up the two OpenPLC v3
containers, serves the whole-plant view, and opens it in a browser tab. The three
units run on the **real** PLC pair over Modbus — one BPCS slave carries control
for all three units, one SIS slave carries safety (each unit in its own offset
register block). It falls back to the fake-PLC path if Docker is unavailable.
```bash
# one-time: Python deps + build the OpenPLC image
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd plc && docker compose build && cd ..

./run_plant.sh start      # containers up + load multi-unit programs + open BOTH tabs
./run_plant.sh stop       # (start|stop|restart|status)
```
It opens two browser tabs off `http://127.0.0.1:8092`:
- **`/`** — the 3D **ground-truth** train, straight from the physics (unspoofable).
- **`/hmi`** — the **operator HMI** mimic of the whole process (crude feed →
  INFLOW/FILL valve → T-101 → R-301 → V-201 → relief/FLARE → P-401 →
  OUTFLOW/EXPORT valve), every value polled from the PLCs (spoofable).

Put them side by side: under a SIS bypass the HMI freezes green while the 3D runs
to failure. Each page's header shows a live-connection chip (**LIVE OpenPLC —
BPCS+SIS connected**, or **SIM — fake PLCs** on the fallback). Equivalent to
`.venv/bin/python -m ui.plant_server --live` once the containers are up.

**Single-unit live demos / tests** still use the per-scenario programs:
```bash
cd plc && docker compose up -d && cd ..
.venv/bin/python -m plc.load_scenario tank      # one scenario; or --plant for all three units
.venv/bin/python -m bridge.run_live_demo tank   # terminal demo over real Modbus
PETROTWIN_LIVE=1 .venv/bin/python -m unittest tests.test_bridge_live tests.test_plant_live -v
```

**Baseline vs. attack.** With no attacker the BPCS holds the process at its safe
setpoint and the SIS never trips — the baseline just runs. The BPCS now **clamps**
an out-of-range setpoint, so the old off-scale-setpoint attack is neutralised; the
evolved attack instead flips the BPCS to **MANUAL** and drives the actuator output
directly (feed wide open / coolant shut) **and** sets the SIS bypass (suppressing
the trip and freezing the HMI). Launch it from the CLI (or the dashboard's "Inject
attack" button, which makes the same writes):
```bash
# --plant targets the connected whole-plant stack (this dashboard); omit it
# only against the single-unit `plc.load_scenario <id>` (no --plant) demo above.
.venv/bin/python -m attack.attack reactor --plant               # MANUAL drive + SIS bypass (full)
.venv/bin/python -m attack.attack reactor --plant --no-bypass   # MANUAL drive, SIS left armed (caught)
.venv/bin/python -m attack.attack reactor --plant --setpoint-only  # naive setpoint tamper (clamped -> caught)
.venv/bin/python -m attack.attack reactor --plant --reset       # stand down (AUTO, clear bypass, pulse SIS reset)
```
The whole-plant view adds a **"leave SIS armed"** checkbox (the defense-in-depth
contrast), a **"Reset SIS"** button, clickable **FILL / EXPORT** valves (real PLC
coils), per-unit HMI alarm chips (AUTO/MANUAL, SIS BYPASS, PV BAD, SP CLAMPED, H,
PVHI/PVLO), and a live **P-401** product-tank level.

## Layout
```
common/       scenarios.py — per-scenario truth (PV, scaling, thresholds, physics)
              registers.py — authoritative Modbus map + per-unit offsets (unit_addr)
              + tests/test_{scenarios,registers,registers_multiunit}.py
physics/      base.py + tank.py / vessel.py / reactor.py ODE models (ground truth)
              make_model(id) factory   + tests/test_{tank,vessel,reactor}.py
plc/          bpcs.st (setpoint-control template), sis.st (trip template), load_scenario.py
              (per-scenario single-unit + generated 3-instance plant programs),
              docker-compose (OpenPLC v3)   + tests/test_plc_{programs,plant_program}.py
bridge/       scenario-agnostic Modbus bridge loop; fake + real PLC clients (ModbusUnitView
              projects each unit's offset block); single-unit + connected-plant bridges; demos
              + tests/test_{bridge,plant}.py (fake) / test_{bridge,plant}_live.py (real, opt-in)
ui/           plant_server.py — whole connected-plant server (live OpenPLC via --live,
              or fake PLCs); one BPCS + one SIS carry all three units. Serves two tabs:
              static/plant.html (3D ground truth, /) + static/hmi.html (operator mimic, /hmi)
attack/       attack.py — external Modbus attacker: force BPCS setpoint + SIS_OVERRIDE
```

Default tests are dependency-free (stdlib `unittest`); `pymodbus`/`pyserial`
are only needed for the live path. Phase-0 HMI is a lightweight custom web app
(the "FUXA or similar" the docs allow); FUXA/OPC-UA/etc. are later phases.
