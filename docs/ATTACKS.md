# ATTACKS.md — attack vectors, consequences, and defences

**Purpose & scope.** PetroTwin is a *defensive* OT-security **testbed** — a software
simulation for research and training. This document catalogues the attack vectors
against a plant of this kind (a BPCS + SIS protecting a hazardous process), the
consequence of each, and — for the vectors this testbed actually implements — how
to perform them **against the local simulated PLCs**. For real-world ICS vectors
that this Phase-2 testbed does *not* implement, the mechanism and consequence are
described at the level of the public [MITRE ATT&CK for ICS](https://attack.mitre.org/matrices/ics/)
knowledge base, mapped to the roadmap phase that would add them — not as an
operational playbook against production plant.

Three honesty boundaries carried from `OVERVIEW.md` / `CLAUDE.md`:

- This reproduces the Triton **attack pattern** (seize the control actuator +
  force-override the safety logic + blind the operator), **not** Triton's
  **mechanism** (a TriStation/Triconex proprietary-protocol exploit and firmware
  implant). OpenPLC ≠ Triconex.
- Phase 2 is **Modbus-only**. Every *performable* vector below is a Modbus
  coil/register write against the two simulated OpenPLC slaves — the same network
  surface the HMI and bridge use. Network/protocol vectors (OPC-UA, segmentation,
  pcap) and the Triton mechanism are later phases.
- The 3D view is driven **directly from physics**, never from the HMI/PLC, so it
  is the one **unspoofable** ground truth. That asymmetry is the whole point: it
  is what every detection recommendation below leans on.

---

## 1. The attack surface (what is writable, and why)

Authoritative map: `common/registers.py` / `ARCHITECTURE.md`. A plant of this kind
exposes two Modbus slaves — the **BPCS** (control) and the **SIS** (safety). The
points an attacker on the Modbus network can write, and the design reason each is
writable, are:

| Point (single-unit addr) | Slave | Attacker use |
|--------------------------|-------|--------------|
| `BPCS_SETPOINT` `%MW1` (holding 1025) | BPCS | operator setpoint — write off-scale to try to saturate the actuator |
| `BPCS_MAN_MODE` coil 0 | BPCS | flip AUTO→MANUAL to take the control law out of the loop |
| `BPCS_MAN_OUT` `%MW2` (holding 1026) | BPCS | in MANUAL, command the actuator position directly |
| `BPCS_OPV_CMD` coil 4 | BPCS | operator export valve (no interlock) |
| `SIS_OVERRIDE` coil 2 | SIS | the maintenance **bypass (MOS)** — suppress the trip, freeze the HMI |
| `SIS_RESET` coil 3 | SIS | pulse the reset pushbutton (clear/replay a latched trip) |
| `SIS_BLK_OPEN_CMD` coil 6 | SIS | tank fill/block valve open request |
| `*_PV` `%MW0` (holding 1024) | both | the sensor word the program reads — false-data injection surface |

> **Live multi-unit addressing.** On the live stack all three units share two
> slaves, each unit in its own **offset block** (`registers.unit_addr`, strides
> coil 16 / %MW 16 / %QW 8; unit 0 = tank unchanged). So the *same* logical write
> targets a different physical address per unit — e.g. `SIS_OVERRIDE` is coil `2`
> for tank, `18` (`%QX2.2`) for reactor, `34` (`%QX4.2`) for vessel. An external
> attacker must know the target unit's block; `attack/attack.py --plant` and
> `bridge.plc_io.ModbusUnitView` compute it, so the one write still just says
> which unit.

Why these are writable and not simply locked: the setpoint, mode, manual output,
reset and valve commands are legitimate operator/maintenance controls. The attack
abuses *authorised* control points reachable over an *unauthenticated* protocol —
which is precisely the real-world ICS problem. (Modbus has no authentication.)

---

## 2. Performable vectors (implemented in this testbed, over Modbus)

Each is a real write the repo can make via `attack/attack.py`, the whole-plant
HMI buttons, or the PLC bridge. Commands assume the venv and both PLCs up and
running the loaded program(s) (`./run_plant.sh start`, or `plc.load_scenario`).
The `attack.attack` commands below target the **connected whole-plant** live
stack (`--plant`, i.e. `run_plant.sh`/`ui.plant_server --live`); drop `--plant`
only against the single-scenario stack (`plc.load_scenario <id>` with no
`--plant`, `bridge.run_live_demo`) — see "Live multi-unit addressing" above.

### A. Safety override / MOS abuse — *defeat the protective trip* **(core Triton pattern)**
- **MITRE ICS:** T0858 *Change Operating Mode*, T0878 *Alarm Suppression*.
- **Mechanism.** Set `SIS_OVERRIDE`. The SIS reframes this as a maintenance
  **bypass (MOS)**: while set, it forces `TRIP_CMD`/`TRIP_STATUS` false and stops
  refreshing `PV_HMI`, so the protective trip cannot fire and the operator's
  displayed value **freezes** at its last nominal reading.
- **Perform (testbed).** `sis.write_coil(R.SisCoil.OVERRIDE, True)` — or, combined
  with the actuator seizure below, `python -m attack.attack reactor --plant`.
- **Consequence.** The independent safety layer is gone. Any hazardous excursion
  now runs unchecked; the operator sees "armed / normal".
- **Detect / defend.** The SIS annunciates `SIS_BYPASS_ACTIVE` (the one honest
  tell) — monitor it out-of-band. A real SIS requires a physical key/MOS
  authorisation and logs bypasses; alarm on any bypass during normal operation.
  The unspoofable 3D ground truth diverges from the frozen HMI.

### B. Control-actuator seizure via MANUAL mode — *drive the process to the edge*
- **MITRE ICS:** T0855 *Unauthorized Command Message*, T0831 *Manipulation of Control*.
- **Mechanism.** Set `BPCS_MAN_MODE=1` and write `BPCS_MAN_OUT` to the dangerous
  extreme (feed valve wide open, or the reactor's coolant valve shut). MANUAL
  takes the proportional control law — *and the operator setpoint clamp* — out of
  the loop, so the actuator obeys the attacker directly.
- **Perform (testbed).** `python -m attack.attack reactor --plant --no-bypass`
  (manual drive only), or the HMI "Inject attack" with *leave SIS armed* checked.
- **Consequence.** The process variable is pushed toward failure (overfill /
  overpressure / runaway) at the maximum rate the actuator allows.
- **Detect / defend.** A healthy SIS still trips at its high-high threshold and
  holds the process safe — this is the **defence-in-depth contrast** the testbed
  demonstrates. Alarm on unexpected AUTO→MANUAL transitions; rate-limit the
  actuator (the BPCS does); independent high-high interlock.

### C. Combined full attack — *B + A together* **(the demo)**
- **Mechanism.** Seize the actuator (B) **and** bypass the SIS (A) in one go.
- **Perform (testbed).** `python -m attack.attack reactor --plant` (default), or
  HMI "Inject attack" with the box unchecked.
- **Consequence.** The process runs **past the failure threshold** while the HMI
  stays green and armed — defeat the SIS, blind the operator, run to failure.
- **Detect / defend.** Only an out-of-band, physics-derived view (the 3D ground
  truth, or independent instrumentation) reveals it. This is the testbed's
  central detection insight.

### D. Operator blinding / HMI spoof — *the "green while burning" effect*
- **MITRE ICS:** T0832 *Manipulation of View*, T0878 *Alarm Suppression*.
- **Mechanism.** A consequence of A: under bypass the SIS freezes `PV_HMI` and
  holds `TRIP_STATUS` clear, so the SCADA poll returns stale-nominal values. (The
  HMI reads the SIS registers over Modbus — a spoofable tap by design.)
- **Perform (testbed).** Any attack that sets `SIS_OVERRIDE` (A/C).
- **Consequence.** The control room has no indication of the excursion; response
  is delayed until physical signs appear.
- **Detect / defend.** Cross-check HMI values against an independent source;
  watch for a *frozen* (unchanging) PV, which is itself anomalous. The 3D view is
  the reference truth here.

### E. Setpoint tampering (naive) — *neutralised, kept as a teaching contrast*
- **MITRE ICS:** T0836 *Modify Parameter*.
- **Mechanism.** Write an off-scale `BPCS_SETPOINT` hoping the control law drives
  the actuator to saturation.
- **Perform (testbed).** `python -m attack.attack reactor --plant --setpoint-only`.
- **Consequence.** **Defeated** by the operator-setpoint **clamp** `[SP_LO,SP_HI]`:
  the BPCS clamps the value and annunciates `SP_CLAMPED`; tank/vessel settle at the
  clamp (below trip). (The reactor's exotherm still reaches its trip even at the
  clamp, so there a healthy SIS catches it.) This is *why* the evolved attack
  moved to MANUAL mode (B).
- **Detect / defend.** Setpoint clamp + `SP_CLAMPED` alarm; range-check all
  operator writes. Shows that a single hardening measure can retire a whole vector.

### F. Final-element / valve manipulation — *misuse the real coils*
- **MITRE ICS:** T0855 *Unauthorized Command Message*.
- **Mechanism.** The terminal valves are real coils: `SIS_BLK_OPEN_CMD` (tank
  fill/block valve) and `BPCS_OPV_CMD` (P-401 export valve, no interlock). Writing
  them forces fill/export state directly.
- **Perform (testbed).** HMI FILL/EXPORT valve clicks (`POST /api/valve`), or
  `bpcs.write_coil(R.BpcsCoil.OPV_CMD, False)` / `sis.write_coil(R.SisCoil.BLK_OPEN_CMD, …)`.
- **Consequence.** E.g. holding the export valve shut backs up P-401; closing the
  fill valve starves the plant. Interlocked valves still obey the trip
  (`BLK_OUT = cmd AND NOT trip`), so this is weaker than A/B unless combined with A.
- **Detect / defend.** Interlock final elements on the SIS trip (the block valve
  is); alarm on operator-valve commands inconsistent with the process state.

### G. False sensor / PV injection — *lie to the logic*
- **MITRE ICS:** T0856 *Spoof Reporting Message*, T0806 *Brute Force I/O*.
- **Mechanism.** The program reads its PV from a shared `%MW` memory word. An
  attacker who writes `*_PV` injects a false process value into the BPCS/SIS logic
  itself (distinct from D, which only fakes the *display*).
- **Perform (testbed).** `bpcs.write_holding(R.BpcsHolding.PV, <false_reg>)` /
  `sis.write_holding(R.SisHolding.PV, …)`. (In normal operation the bridge owns
  this write every scan; an attacker racing or isolating the bridge can inject.)
- **Consequence.** Feed a low PV → the SIS never sees the hazard and never trips
  (functionally like A but through the sensor path); feed a high PV → nuisance
  trip / DoS. The SIS `PV_BAD` range check catches *out-of-range* spoofs and
  fails safe, so a credible attacker stays in-range.
- **Detect / defend.** Transmitter validity/range checks (the SIS has `PV_BAD`),
  rate-of-change limits, sensor redundancy/voting, and signing/authenticating the
  sensor path (not available in plain Modbus — hence the segmentation argument).

### H. Cross-unit coupling exploitation — *attack a neighbour through the process*
- **MITRE ICS:** T0831 *Manipulation of Control* (second-order effect).
- **Mechanism.** The connected plant is forward-coupled (`T-101`→`R-301`→`V-201`:
  tank overfill warms the reactor; reactor runaway pressurises the vessel).
  Attacking the *upstream* unit disturbs the downstream one even if the downstream
  PLCs are untouched.
- **Perform (testbed).** Attack one unit (A–C) and watch the coupling animate in
  the whole-plant view / `bridge.run_plant_demo`.
- **Consequence.** A single compromised unit can push a neighbour toward its own
  limit; the downstream unit's *healthy* SIS may be the last line of defence.
- **Detect / defend.** Plant-wide (not per-unit) monitoring; model the couplings
  so a downstream excursion with a quiet local cause flags the upstream unit.

### I. Reset / latch manipulation — *replay or block recovery*
- **MITRE ICS:** T0881 *Service Stop*, T0838 *Modify Alarm Settings* (analogue).
- **Mechanism.** Pulse `SIS_RESET` to clear a latched trip, or spoof conditions so
  a legitimate reset is blocked/replayed. The SIS gates reset on a deadband
  (`PV < TRIP_HH − RESET_DB`) and a good PV, which limits abuse.
- **Perform (testbed).** `sis.write_coil(R.SisCoil.RESET, True/False)`, or
  `python -m attack.attack reactor --plant --reset` (the stand-down path).
- **Consequence.** Clearing a trip while the hazard persists (if the deadband is
  defeated via G) re-exposes the process; blocking reset is a recovery-phase DoS.
- **Detect / defend.** Deadband + good-PV gating (implemented); require manual,
  authenticated reset; log every reset.

---

## 3. Real-world vectors NOT performable in Phase 2 (roadmap-mapped)

These are the vectors a real plant faces that this testbed does not yet implement.
Described at the public-technique level, with the phase that would add them.

### J. Network access & flat segmentation — **Phase 3**
- **MITRE ICS:** T0883 *Internet Accessible Device*, T0886 *Remote Services*.
- **Mechanism / consequence.** Every performable vector above presumes the
  attacker already reaches the Modbus network. In the real world that reach comes
  from flat networks, dual-homed hosts, exposed remote access, or a compromised
  IT/OT boundary. Without Purdue-level segmentation one foothold reaches both the
  BPCS and SIS — exactly the co-location Triton exploited.
- **Defence.** Zones/conduits (IEC 62443), SIS on a separate network, unidirectional
  gateways, no routable path from IT to the safety network. *Phase 3 adds
  segmentation + pcap so the traversal is observable.*

### K. OPC-UA / additional-protocol attacks — **Phase 3**
- **Mechanism / consequence.** Richer protocols add authentication but also
  larger attack surface (certificate handling, discovery, deserialization).
- **Defence.** Mutual auth, certificate pinning, least-privilege address-space
  exposure. *Phase 3 adds OPC-UA alongside Modbus.*

### L. Triton mechanism — safety-protocol exploit + firmware implant — **Phase 4**
- **MITRE ICS:** T0820 *Exploitation for Evasion*, T0857 *System Firmware*, T0874
  *Hooking*.
- **Mechanism / consequence.** The real Triton used the proprietary TriStation
  protocol (UDP/1502) to download a malicious implant into a Triconex MP's
  firmware — subverting the safety controller itself, not merely writing a coil.
  That is strictly more powerful and stealthier than the Modbus override pattern
  this testbed shows.
- **Defence.** Keypair/authenticated program download, controller key-switch in
  RUN (not PROGRAM), firmware integrity attestation, TriStation monitoring.
  *Phase 4 emulates a TriStation-like protocol so the attack exploits a protocol,
  not a register write. Real Triconex hardware + original-malware analysis is the
  explicit out-of-scope ceiling.*

### M. Engineering-workstation compromise / malicious logic download
- **MITRE ICS:** T0873 *Project File Infection*, T0843 *Program Download*.
- **Mechanism / consequence.** Compromise the engineering workstation and push
  altered ST/ladder logic to the BPCS or SIS — persistent, survives reboots, and
  can hide its own effect. In this testbed the loader (`plc/load_program.py`,
  `plc/load_scenario.py`) is the analogue of that download path.
- **Defence.** Signed projects, change control, workstation hardening, read-only
  SIS logic, and detecting unexpected `compile`/`start_plc` actions.

### N. Supply-chain / initial access (phishing, removable media, vendor access)
- **MITRE ICS:** T0862 *Supply Chain Compromise*, T0865 *Spearphishing Attachment*.
- **Mechanism / consequence.** The real initial-access routes that precede any of
  the above. Out of scope for the process-simulation testbed.
- **Defence.** Vendor vetting, media control, awareness, MFA on remote access.

---

## 4. The detection insight (why the testbed exists)

Every blinding/override vector (A, C, D, G) succeeds **only against instruments
the attacker can reach**. The defence the testbed proves is an **out-of-band,
physics-derived ground truth** the attacker cannot write:

- The **3D view is driven directly from physics**, never from the HMI/PLC — so it
  diverges visibly from a spoofed HMI. Real-world analogues: independent/hardwired
  instrumentation, a separate historian the control path can't write, physics-based
  anomaly detection comparing measured vs. expected process behaviour.
- **Defence in depth**: a healthy SIS (not bypassed) still trips (vector B), so the
  override step (A) is *necessary* for the full attack — monitoring the bypass
  annunciation (`SIS_BYPASS_ACTIVE`) retires the stealth.
- **Hardening retires vectors**: the setpoint clamp (E) neutralises the naive
  attack outright; range/validity checks (G) force the attacker in-range; final-
  element interlocks (F) blunt valve abuse.

## 5. Countermeasure summary

| Vector | Primary countermeasure (in testbed / real) |
|--------|---------------------------------------------|
| A, C, D | Monitor `SIS_BYPASS_ACTIVE` out-of-band; physical MOS key + bypass logging; independent ground truth |
| B | Healthy SIS high-high interlock; alarm AUTO→MANUAL; actuator rate limit |
| E | Setpoint clamp + `SP_CLAMPED` alarm; range-check operator writes |
| F | Interlock final elements on trip; command/state consistency alarms |
| G | Transmitter validity/range (`PV_BAD`), rate-of-change, sensor voting, authenticated sensor path |
| H | Plant-wide (coupling-aware) monitoring |
| I | Deadband + good-PV + manual authenticated reset; reset logging |
| J–N | Segmentation (IEC 62443), authenticated protocols, signed program download, SIS key-switch in RUN, supply-chain controls |

## 6. References

- Internal: `OVERVIEW.md` (scope & honesty limits), `ARCHITECTURE.md` (register
  map + per-unit offsets), `ROADMAP.md` (phases 3/4/5), `attack/attack.py`
  (the performable vectors), `plc/bpcs/bpcs.st` + `plc/sis/sis.st` (the logic
  the defences live in).
- External: MITRE ATT&CK for ICS; IEC 62443 (zones & conduits); IEC 61511 (SIS);
  public TRITON/TRISIS incident analyses (the attack pattern reproduced here).
