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
- **Control PLC (BPCS) — setpoint behaviour.** Control logic drives the actuator to
  hold setpoint under normal inputs; it does **not** perform the safety trip (that is
  the SIS's job) — confirming the two-PLC separation.

Pass = trip asserts exactly at threshold with correct actuator; BPCS holds setpoint
and does not substitute for the SIS.

## 3. Modbus bridge (round-trip tests)

- **Sensor path (physics → PLC).** Write a known physics value; read it back from the
  PLC input register; assert equal within scaling tolerance.
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

This is the test that defines "done" for the protected 2-day demo. Run on the single
anchor scenario.

**Sequence**
1. **Normal.** Start physics, both PLCs, bridge, HMI, 3D view. Process steady; SIS
   armed; HMI nominal (green); 3D stable. 3D value tracks HMI value.
2. **Attack.** Run the attack script: override the SIS trip and freeze the HMI-polled
   registers.
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

## Scope note

These tests validate the attack **pattern** and the **detection insight**, within a
software simulation. They do not validate Triton **mechanism** fidelity (TriStation /
Triconex), which is explicitly out of Phase 0 — see `OVERVIEW.md` and `ROADMAP.md`.
