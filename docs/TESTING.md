# PetroTwin — Testing & Validation

How each layer is validated independently, and how the integrated loop is proven. The
goal is that the Phase-0 demo's cause-and-effect is **trustworthy**: when the 3D view
shows rupture, we must know that reflects the true physics and not a bug.

## 1. Physics layer (unit tests)

The ODE model is the ground truth, so it is tested most rigorously.

- **Conservation checks.** With no sources/sinks acting, conserved quantities stay
  conserved (e.g. closed tank: mass constant to within integrator tolerance; no
  spurious energy gain in the reactor model).
- **Known steady states.** Given a constant input, the state converges to the
  analytically expected steady value (e.g. inflow = outflow → level constant; balanced
  heat generation/removal → stable temperature).
- **Monotonic failure behaviour.** With the protective action disabled and the driving
  input applied, the relevant variable moves monotonically toward the failure
  threshold and crosses it (overpressure rises, level overfills, reactor temperature
  runs away).
- **Numerical sanity.** No NaN/Inf; results stable under a halved timestep (solution
  does not change meaningfully → integration is converged, not artefactual).

Pass = all conservation and steady-state tests within tolerance; failure trajectory
crosses the defined threshold in bounded time.

## 2. PLC logic (trip-threshold tests)

Tested per OpenPLC instance against its IEC 61131-3 logic.

- **Safety PLC (SIS) — trip fires at the right threshold.** Feed the SIS a rising
  measured value; assert the trip output asserts **at** the configured limit (not
  before, not after) and commands the correct protective actuator (relief / feed-close
  / cooling).
- **Hysteresis / latch.** Once tripped, the SIS stays tripped (or follows the defined
  reset rule) rather than chattering around the limit.
- **Control PLC (BPCS) — setpoint behaviour.** The proportional control loop drives
  the actuator to hold the PV at its writable, **clamped** setpoint under normal
  inputs (and the signed gain means the reactor's coolant valve opens as it heats);
  it does **not** perform the safety trip (that is the SIS's job) — confirming the
  two-PLC separation. The setpoint **clamp** (`[SP_LO, SP_HI]`) neutralises a naive
  off-scale-setpoint attack; the evolved attack therefore uses **MANUAL mode**
  (`BPCS_MAN_MODE` + `BPCS_MAN_OUT`) to drive the actuator to failure, which the
  bridge tests exercise both with the SIS armed (caught) and bypassed (fails).
  Additional unit tests cover the AUTO/MANUAL bumpless transfer, the output rate
  limit, and the SIS pre-alarm / bad-PV / deadband-gated reset / block-valve logic.

Pass = trip asserts exactly at threshold with correct actuator; BPCS holds setpoint
and does not substitute for the SIS.

## 3. Modbus bridge (round-trip tests)

- **Sensor path (physics → PLC).** Write a known physics value; read it back from the
  PLC input register; assert equal within scaling tolerance. Note: the sensor must be
  injected into a `%MW` **memory word** (holding `1024`), not a `%QW` output — OpenPLC
  rewrites a `%QW` from the program image every scan, so a master write there never
  reaches the program (see `ARCHITECTURE.md`). The live round-trip and trip tests must
  assert the *program reacts* (trip fires at threshold), not just immediate read-back,
  or they false-pass on a clobbered register.
- **Actuator path (PLC → physics).** Set a known PLC output coil/register; assert the
  bridge applies the matching command to the physics model.
- **Register-map conformance.** Every row in the authoritative register map
  (`ARCHITECTURE.md`) round-trips with correct type, address, scale, and direction.
- **Timing / sync.** Measure per-cycle latency and jitter across the four processes;
  assert loop drift stays within the budget needed for a readable demo. (This is the
  project's biggest risk — test it early and watch it.)

Pass = every mapped register round-trips correctly; loop drift within budget.

## 4. HMI (read checks)

- **Faithful read under normal operation.** HMI-displayed values match the PLC
  registers they poll, which match the physics (within scaling), while nothing is
  attacking.
- **Spoof visibility.** When the attack freezes/clamps the HMI-polled registers, the
  HMI continues to display nominal values — i.e. the HMI faithfully shows what the
  (compromised) PLC reports. This is expected behaviour, and it is what makes the HMI
  spoofable.

Pass = HMI matches PLC registers in both normal and attacked states (the HMI is an
honest mirror of a dishonest source).

## 5. End-to-end acceptance test (the Phase-0 demo)

This is the test that defines "done" for the protected 2-day demo. It now runs on
**each of the three scenarios** (tank / vessel / reactor) — every one must pass the
A–G criteria below. `bridge.run_fake_demo <scenario>` and `bridge.run_live_demo
<scenario>` print the acceptance verdict; the parameterized bridge tests assert it.

**Sequence**
1. **Baseline.** Start physics, both PLCs, bridge, HMI, 3D view. The BPCS holds the
   process at its setpoint; SIS armed; HMI nominal (green); 3D stable. 3D value
   tracks HMI value, and with no attacker it stays there indefinitely (never trips).
2. **Attack.** Run the external attack script: flip `BPCS_MAN_MODE` and drive
   `BPCS_MAN_OUT` to the extreme (drives the process) and set `SIS_OVERRIDE` (suppress
   the trip + freeze the HMI-polled registers). (Manual-drive with the SIS left armed
   is also tested and must be caught by the healthy SIS; a naive setpoint tamper is
   tested to confirm the clamp neutralises it — no failure.)
3. **Divergence.** Physics continues integrating true state. The protective action
   never fires. The true variable crosses the **failure threshold**; the 3D view shows
   the process going to failure (rupture/overflow/runaway) — while the HMI **stays
   green**.

**Pass/fail criteria for the 2-day demo acceptance**

| # | Criterion                                                                                       | Pass requires |
|---|-------------------------------------------------------------------------------------------------|---------------|
| A | Before attack: HMI value and 3D value agree (within scaling tolerance), SIS shows **armed**.    | Agreement + armed |
| B | After attack: SIS trip/interlock does **not** fire; protective actuator command stays inactive. | No trip |
| C | After attack: the **true** process variable crosses the defined failure threshold.              | Threshold crossed |
| D | After attack: the **3D ground-truth view** reflects the failure (reaches rupture/overflow/runaway state). | 3D shows failure |
| E | After attack: the **HMI remains nominal** (stays "green" / within normal band) throughout.       | HMI stays nominal |
| F | **Divergence proven:** at the moment of threshold crossing, |3D true value − HMI reported value| exceeds a clear, pre-agreed margin. | Divergence ≥ margin |
| G | The 3D view is driven **directly from physics**, not from the HMI/PLC (verified by construction/code review). | Direct tap only |

**Demo is accepted iff A–G all pass in a single live run.** Criterion F (quantified
divergence) and criterion G (independent data path) together are what make the demo a
genuine demonstration of "HMI spoofable, ground truth not" rather than a scripted
animation.

## 6. Connected-plant acceptance test (Phase 1)

Phase 1 runs all three units as ONE flowsheet (T-101 feed tank → R-301 reactor →
V-201 vessel, forward-coupled — see `ARCHITECTURE.md` / `physics/plant.py`). Its
"done" criteria are proven by `tests/test_plant.py` and made visible by
`python3 -m bridge.run_plant_demo [tank|reactor|vessel]` (fake PLCs, one BPCS +
one SIS per unit; no Docker). Each criterion below must hold:

| # | Criterion | Pass requires |
|---|-----------|---------------|
| P1 | **Connected steady state.** With no attacker, every unit holds near its setpoint and no SIS trips, indefinitely. | All three hold; zero trips |
| P2 | **Baseline-zero couplings.** At the operating point every forward coupling is zero (so steady state is exact, not a balance of disturbances). | `q_coupling == p_coupling == 0` at setpoint |
| P3 | **Targeted failure.** Attacking one unit (MANUAL drive + `SIS_OVERRIDE`) drives THAT unit past its failure threshold while its HMI stays frozen/armed (large spoof gap). | Target fails; HMI frozen; gap > 0.4·fail_at |
| P4 | **No neighbour failure.** The up/downstream units do not fail during a single-unit attack. | Only the target reaches failure |
| P5 | **Forward coupling.** An upstream attack visibly moves its downstream neighbour (overfilled tank warms the reactor; runaway reactor pressurises the vessel); a downstream attack leaves upstream units undisturbed (no back-pressure in Phase 1). | Downstream responds; upstream static |
| P6 | **Neighbour defence-in-depth.** When a reactor runaway pressurises the vessel, the vessel's own HEALTHY SIS trips and its relief holds it safe — even though the attacked unit's SIS is overridden. | Neighbour SIS trips, holds < rupture |
| P7 | **Healthy-SIS contrast.** A manual-drive attack with the SIS left armed is caught: the targeted unit trips and is held safe. | Target does not fail |

The couplings are **plausible, hand-tuned, not validated** — the gains make an
upstream attack visibly move its neighbour without instantly destroying it, not
fitted to plant data. Validated connected physics is the final OpenModelica phase
(`ROADMAP.md`). Phase 1 acceptance runs on the in-memory PLCs, exactly as the
Phase-0.5 acceptance does (§5).

The **whole-plant visualization** (Phase 2) runs on that same in-memory-PLC path
and on the **live OpenPLC pair**: `./run_plant.sh start` (or
`python3 -m ui.plant_server [--live]`, port 8092) serves a view in which all
seven criteria above are *watchable* as one flowsheet — a spoofable per-unit HMI
tile strip (each polled from that unit's SIS) over one unspoofable 3D
ground-truth train (T-101 → R-301 → V-201), with the attacked unit highlighted
and the forward couplings animated as the interconnecting streams. Attack any
unit from the page (full attack, or the manual-drive-with-SIS-armed
defence-in-depth contrast), toggle the FILL/EXPORT valves, and reset a latched
trip; the HMI tiles carry mode/alarm/bypass chips and a live P-401 level, and a
header chip shows whether the data is from real OpenPLC or the fake fallback.

The **live OpenPLC stack** carries all three units on the two existing slaves —
one BPCS for all three units' control, one SIS for all three units' safety — via
per-unit offset register blocks (`registers.unit_addr`, applied by
`bridge.plc_io.ModbusUnitView`) and one generated 3-instance ST program per slave
(`plc.load_scenario.load_plant`). Verified opt-in by `tests/test_plant_live.py`
(`PETROTWIN_LIVE=1`): baseline holds all three at setpoint on the real BPCS, and
attacking one unit opens a spoof gap (true PV diverges from the frozen, armed HMI)
while the other two hold. Phase 2 is complete.

## Scope note

These tests validate the attack **pattern** and the **detection insight**, within a
software simulation. They do not validate Triton **mechanism** fidelity (TriStation /
Triconex), which is explicitly out of Phase 0 — see `OVERVIEW.md` and `ROADMAP.md`.
