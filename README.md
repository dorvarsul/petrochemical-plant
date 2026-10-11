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

## Phase 0 — status: **tank-overfill scenario, working end to end (live)**

The anchor scenario is **tank overfill → high-level trip**. The full divergence
runs both offline (fake PLCs) and live (two real OpenPLC v3 instances over Modbus).

## Quick start

### Offline — no Docker (pure stdlib)
```bash
python3 -m unittest discover -s tests      # 34 tests
python3 -m bridge.run_fake_demo            # normal -> attack -> overflow, HMI frozen
```

### Live — Docker + real OpenPLC + the dashboard
```bash
# 0. Python deps for the live path
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 1. Build + start the two OpenPLC v3 slaves
cd plc && docker compose build && docker compose up -d && cd ..

# 2. Load + compile + start the two ST programs (headless)
python3 plc/load_program.py http://127.0.0.1:8081 plc/bpcs/bpcs.st "BPCS fill control"
python3 plc/load_program.py http://127.0.0.1:8082 plc/sis/sis.st  "SIS high-level trip"

# 3. (optional) verify the live Modbus round-trip
PETROTWIN_LIVE=1 .venv/bin/python -m unittest tests.test_bridge_live -v

# 4a. Terminal demo over real Modbus
.venv/bin/python -m bridge.run_live_demo

# 4b. Or the split-screen dashboard (HMI | 3D ground truth)
.venv/bin/python -m ui.server        # open http://127.0.0.1:8090 , click "Inject attack"
```

The attack can also be launched from the CLI:
```bash
.venv/bin/python -m attack.attack            # set SIS_OVERRIDE
.venv/bin/python -m attack.attack --reset    # clear it
```

## Layout
```
physics/      tank ODE model (ground truth)            + tests/test_tank.py
common/       registers.py — authoritative Modbus map  + tests/test_registers.py
plc/          bpcs.st, sis.st, docker-compose, load_program.py  (OpenPLC v3)
bridge/       Modbus bridge loop; fake + real PLC clients; demos
              + tests/test_bridge.py (fake) / test_bridge_live.py (real, opt-in)
ui/           server.py + static/dashboard.html (HMI + Three.js 3D)
attack/       attack.py — SIS_OVERRIDE over Modbus
```

Default tests are dependency-free (stdlib `unittest`); `pymodbus`/`pyserial`
are only needed for the live path. Phase-0 HMI is a lightweight custom web app
(the "FUXA or similar" the docs allow); FUXA/OPC-UA/etc. are later phases.
