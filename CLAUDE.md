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
- Tank overfill → high-level trip (single ODE: level; the anchor)
- Vessel overpressure → relief valve (single ODE: pressure)
- Reactor runaway → cooling-jacket trip (2 coupled ODEs: temp+conc., Arrhenius)
Pick/run a scenario with the `tank|vessel|reactor` arg or `PETROTWIN_SCENARIO`.

## Baseline control + external setpoint attack
- The BPCS runs a proportional loop holding the PV at a **writable setpoint**
  (`BPCS_SETPOINT`, %MW1): `feed_cmd = clamp(bias + kp·(SP−PV), 0, 1000)`, kp signed
  (negative for the reactor — its actuator is the coolant valve). With no attacker
  the baseline holds setpoint forever and the SIS never trips.
- The attack is external (`attack/attack.py`, Modbus): force an off-scale
  `BPCS_SETPOINT` (drives the process) **and** set `SIS_OVERRIDE` (defeat the trip +
  freeze the HMI). Setpoint-only is caught by the healthy SIS (defense-in-depth).
- Control gains live in `common/scenarios.py` and are substituted into `bpcs.st` by
  `plc/load_scenario.py` (same pattern as the SIS `trip_hh`). The reactor physics is
  coolant-controlled (fixed reactant throughput) and time-dilated for legibility.

## Repo conventions
- Docs in repo root: OVERVIEW / ARCHITECTURE / ROADMAP / TESTING / this file.
- One authoritative Modbus register map (ARCHITECTURE.md); no scattered addresses.
- Keep claims consistent across all docs.

## Run / test
- Run: physics → both OpenPLC instances → Modbus bridge → HMI → 3D view → attack script.
- Test: physics units, PLC trip-threshold, bridge round-trip, HMI read, e2e acceptance (normal → attack → HMI nominal while ground truth passes threshold). See TESTING.md.
