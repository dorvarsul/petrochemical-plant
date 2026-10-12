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
python3 -m unittest discover -s tests          # 68 tests
python3 -m bridge.run_fake_demo                # tank (default)
python3 -m bridge.run_fake_demo vessel         # or vessel / reactor
python3 -m bridge.run_fake_demo reactor        # normal -> attack -> failure, HMI frozen
```

### Live — Docker + real OpenPLC + the dashboard
```bash
# 0. Python deps for the live path
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 1. Build + start the two OpenPLC v3 slaves
cd plc && docker compose build && docker compose up -d && cd ..

# 2. Load + compile + start the BPCS + the SIS program for a scenario (headless)
.venv/bin/python -m plc.load_scenario tank     # or vessel / reactor

# 3. (optional) verify the live Modbus round-trip
PETROTWIN_LIVE=1 .venv/bin/python -m unittest tests.test_bridge_live -v

# 4a. Terminal demo over real Modbus
.venv/bin/python -m bridge.run_live_demo tank  # or vessel / reactor

# 4b. Or the split-screen dashboard (HMI | 3D ground truth)
.venv/bin/python -m ui.server    # open http://127.0.0.1:8090 , pick a scenario, "Inject attack"
```

The dashboard loads the chosen scenario's PLC programs itself (step 2 is for the
terminal demo / tests); its dropdown switches scenarios live by recompiling the PLCs.

**Baseline vs. attack.** With no attacker the BPCS holds the process at its safe
setpoint and the SIS never trips — the baseline just runs. The attack is external,
over Modbus: it writes a malicious control setpoint to the BPCS (driving the process
toward failure) **and** overrides the SIS (suppressing the trip and freezing the
HMI). Launch it from the CLI (or the dashboard's "Inject attack" button, which makes
the same writes):
```bash
.venv/bin/python -m attack.attack reactor            # force setpoint + SIS_OVERRIDE
.venv/bin/python -m attack.attack reactor --setpoint-only  # only the setpoint (SIS catches it)
.venv/bin/python -m attack.attack reactor --reset    # stand down (restore setpoint, clear override)
```

## Layout
```
common/       scenarios.py — per-scenario truth (PV, scaling, thresholds, physics)
              registers.py — authoritative Modbus map   + tests/test_{scenarios,registers}.py
physics/      base.py + tank.py / vessel.py / reactor.py ODE models (ground truth)
              make_model(id) factory   + tests/test_{tank,vessel,reactor}.py
plc/          bpcs.st (setpoint-control template), sis.st (trip template), load_scenario.py,
              docker-compose (OpenPLC v3)   + tests/test_plc_programs.py
bridge/       scenario-agnostic Modbus bridge loop; fake + real PLC clients; demos
              + tests/test_bridge.py (fake) / test_bridge_live.py (real, opt-in)
ui/           server.py + static/dashboard.html (HMI + Three.js full-process 3D, selector)
attack/       attack.py — external Modbus attacker: force BPCS setpoint + SIS_OVERRIDE
```

Default tests are dependency-free (stdlib `unittest`); `pymodbus`/`pyserial`
are only needed for the live path. Phase-0 HMI is a lightweight custom web app
(the "FUXA or similar" the docs allow); FUXA/OPC-UA/etc. are later phases.
