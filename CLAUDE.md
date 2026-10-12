# CLAUDE.md — PetroTwin working agreement
One-liner: four-layer OT security **testbed** (software sim) showing a Triton-style
attack *pattern* — defeat the SIS, blind the operator, run the simulated process to
failure. Defensive research/training; no real plant, no real malware.

## The four layers
1. Physics — Python ODEs, holds TRUE state (OpenModelica later).
2. PLC — OpenPLC ×2: Control PLC (BPCS) + Safety PLC (SIS); SIS is the attack target.
3. HMI/SCADA — FUXA or similar, polls PLCs over Modbus → SPOOFABLE.
4. 3D view — Three.js, driven DIRECT from physics → UNSPOOFABLE.
Demo point: SIS trip overridden → HMI stays green while 3D runs to failure.

## Phase-0 scope boundary (protected 2-day demo)
IN:  ONE scenario, Python physics, OpenPLC control+safety, lightweight HMI, Three.js
     ground truth, one Modbus bridge, one attack script. Modbus-only.
OUT: OpenModelica/FMI, OPC-UA, historian, segmentation, pcap,
     TriStation/Triconex fidelity. Defer all to Phase 1+.
     (The other two scenarios were OUT for the protected demo; added right after
     as Phase 0.5 on Python physics — see Scenarios below.)
Done = live run: normal → inject attack → HMI green while 3D passes failure threshold.

## Key constraints
- Modbus-only in Phase 0. No OPC-UA/FMI/extra scenarios until Phase 1+.
- OpenPLC ≠ Triconex: attack PATTERN, not Triton MECHANISM (TriStation UDP/1502 +
  Triconex firmware). State honestly; don't overclaim.
- 3D view reads physics DIRECTLY — never from HMI/PLC. Violating this invalidates the demo.
- Biggest risk = four-process time-sync over the Modbus bridge, not any one layer.
  Protect Phase 0 by resisting scope creep.

## Scenarios — all three now implemented (Phase 0.5, Python physics)
The protected demo used ONE; all three now exist on pure-Python ODEs and are
selectable through every layer (`common/scenarios.py` is the single source of
per-scenario truth). Still Modbus-only; OpenModelica/validated physics deferred.
The single-unit sims still run one at a time (Phase 0.5). Phase 1 ALSO wires all
three into ONE connected flowsheet — `T-101` → `R-301` → `V-201`, forward-coupled
(tank overfill warms the reactor; reactor runaway pressurises the vessel) — on the
Python + in-memory-PLC path: `physics/plant.py`, `bridge/plant_bridge.py`,
`bridge/run_plant_demo.py`, `tests/test_plant.py`. Couplings are zero at the
baseline operating point, so the whole plant holds a connected steady state; each
scenario is "which unit the attacker targets". Phase 2's whole-plant view runs on
that fake-PLC path AND on the live OpenPLC pair (`ui/plant_server.py` +
`ui/static/plant.html`; `./run_plant.sh start`, or `python3 -m ui.plant_server
[--live]`, port 8092) — a spoofable three-tile HMI strip over one unspoofable 3D
ground-truth train of the whole flowsheet, attack any unit, forward couplings
animated, with a header chip showing live-OpenPLC-vs-fake. TWO tabs off one
`/api/state`: `/` = 3D ground truth (`plant.html`, unspoofable), `/hmi` = operator
mimic of the whole process (`hmi.html`: inflow/outflow valves, all units,
relief/flare, P-401; spoofable). `run_plant.sh start` opens both. The LIVE stack carries
all three units on the TWO existing slaves — one BPCS for all three units'
control, one SIS for all three units' safety — via per-unit OFFSET register blocks
(`registers.unit_addr`, applied by `bridge/plc_io.py` `ModbusUnitView`) and one
GENERATED 3-instance ST program per slave (`plc/load_scenario.py` `load_plant`);
no extra containers, single-unit map stays canonical (unit 0 = tank). Proven by
`tests/test_plant_live.py`. Phase 2 is DONE. Phase 2.5 is a DEPLOYMENT-only
split onto FOUR Ubuntu Linux PCs on one L2 Ethernet segment — PC1 physics+3D,
PC2 control PLC, PC3 safety PLC, PC4 HMI; `localhost:5020/5021` becomes Modbus
TCP to real host IPs, physics+3D stay co-located so the direct tap stays a
local (unspoofable) read, layers/register-map/attack unchanged, still
Modbus-only. **Software done, physical 4-PC run pending**: every host/port is
now read from `common/deploy_config.py` (one env var per address, defaulting
to the old localhost layout — nothing above changes unless you set one); PC4
runs a genuinely standalone, physics-blind HMI (`ui/hmi_server.py`, a Modbus
master with NO import of `physics.plant`, `./run_hmi.sh`); the OpenPLC compose
file is split one-container-per-PC (`deploy/pc2-control-plc/`,
`deploy/pc3-safety-plc/`); `attack/attack.py` gained `--plant` (apply the
per-unit offset so it hits the right unit of the CONNECTED plant instead of
always unit 0/tank — a real pre-existing bug this surfaced and fixed); and
`deploy/` has a README + checklist per PC. All exercised against the real
OpenPLC pair on one host (loopback standing in for the L2); see
`deploy/README.md` "Status" for what that does and doesn't prove, and
`deploy/checklist.md` for the real multi-PC bring-up. OpenModelica is the
FINAL phase.
`run_plant.sh start|stop` is the one-command runner (containers up + serve + open
tab; fake fallback if no Docker; now also probes configured remote PLC hosts
over the network before falling back — see `deploy/pc1-physics-3d/README.md`).
- Tank overfill → high-level trip (single ODE: level; the anchor)
- Vessel overpressure → relief valve (single ODE: pressure)
- Reactor runaway → cooling-jacket trip (2 coupled ODEs: temp+conc., Arrhenius)
Pick/run a scenario with the `tank|vessel|reactor` arg or `PETROTWIN_SCENARIO`.

## Baseline control + evolved attack (real-SIF realism)
- The BPCS runs a proportional loop holding the PV at a **writable setpoint**
  (`BPCS_SETPOINT`, %MW1): `feed_cmd = clamp(bias + kp·(SP−PV), 0, 1000)`, kp signed
  (negative for the reactor — its actuator is the coolant valve). Added realism
  (P-only law unchanged): operator setpoint **clamp** `[SP_LO, SP_HI]`, **AUTO/MANUAL**
  mode (`BPCS_MAN_MODE`/`BPCS_MAN_OUT`, %MW2) with bumpless transfer, and an output
  **rate limit** (`CTRL_RATE_LIM_PM`). The SIS adds a pre-alarm "H", a bad-PV
  fail-safe trip, and a **manual reset with deadband**; `SIS_OVERRIDE` is reframed as
  a maintenance **BYPASS (MOS)** with `SIS_BYPASS_ACT` annunciation.
- The attack is external (`attack/attack.py`, Modbus). Because the clamp defeats the
  old off-scale-setpoint trick, the driver is now **MANUAL mode**: set `BPCS_MAN_MODE`
  + drive `BPCS_MAN_OUT` to the extreme **and** set `SIS_OVERRIDE` (defeat the trip +
  freeze the HMI). `--no-bypass` (manual drive, SIS armed) is the defense-in-depth
  contrast; `--setpoint-only` shows the clamp neutralising the naive attack.
- Terminal valves are real coils: the FILL valve (T-101 inlet) is the tank SIS's
  block-valve final element (`SIS_BLK_OUT`); the EXPORT valve (P-401 outlet) is a
  BPCS operator valve (`BPCS_OPV_OUT`). **P-401** is a terminal level sink
  (`physics/product.py`), not an attack target.
- Control gains, setpoint clamp, and SIS thresholds live in `common/scenarios.py` and
  are substituted into `bpcs.st`/`sis.st` by `plc/load_scenario.py`. The reactor
  physics is coolant-controlled (fixed reactant throughput) and time-dilated for
  legibility. **Lock-step invariant:** `bridge/plc_io.py` `FakeBpcs`/`FakeSis` must
  stay byte-for-byte logically identical to the ST programs.

## Repo conventions
- Docs in repo root: OVERVIEW / ARCHITECTURE / ROADMAP / TESTING / ATTACKS / this file.
- One authoritative Modbus register map (ARCHITECTURE.md); no scattered addresses.
- Keep claims consistent across all docs.

## Run / test
- Run: physics → both OpenPLC instances → Modbus bridge → HMI → 3D view → attack script.
- Test: physics units, PLC trip-threshold, bridge round-trip, HMI read, e2e acceptance (normal → attack → HMI nominal while ground truth passes threshold). See TESTING.md.
