# PetroTwin — OT Cybersecurity Testbed

## What this is

PetroTwin is a four-layer operational-technology (OT) testbed that simulates a
Triton/Trisis-style attack **end to end, entirely in software**, for defensive
security research, detection development, and training.

The scenario it reproduces: an attacker compromises the **safety layer** of an
industrial plant, disables a trip/interlock, and drives a *simulated* process to
physical failure — the final damage step that did **not** occur in the real 2017
Triton incident (the plant tripped safely instead). PetroTwin lets defenders watch
that failure unfold in a lab so they can study how to detect and prevent it.

Nothing here touches a real plant. Every "device" is a simulation or an open-source
soft component running on a workstation.

## Why it exists

The defensive point of the whole testbed is a single idea:

> **The operator's screen can lie. The physics cannot.**

Operators and SCADA systems trust what the HMI shows them. But the HMI only knows
what the PLCs tell it over the network, and a compromised safety controller can be
made to report "all nominal" while the real process runs away. PetroTwin makes that
gap visible and measurable so teams can build detections that do not depend on the
same data path an attacker controls.

## The four layers (in plain terms)

1. **Physics** — the simulated plant itself. A process model (Python ODEs for the
   demo; a validated OpenModelica model later) that holds the **true** state of the
   process: pressure, level, temperature, concentration. This is ground truth.

2. **PLC** — the controllers. Built on **OpenPLC** (IEC 61131-3 logic, Modbus
   interface). There are **two separate instances**:
   - a **Control PLC (BPCS)** that runs the plant day to day, and
   - a **Safety PLC (SIS)** that holds the trip/interlock logic and is supposed to
     shut the process down before it reaches danger.
   The attack targets the **Safety PLC**.

3. **HMI/SCADA** — the operator's screen. A lightweight HMI (FUXA or similar) that
   **polls the PLCs over Modbus**. It only ever sees what the PLCs report, so it can
   be **spoofed or frozen**.

4. **3D view** — the "ground-truth" visualization. A Three.js scene driven
   **directly from the physics model**, never through the HMI or the PLCs. Because it
   reads the true state, it **cannot be spoofed** by a network-layer attack.

## The core demonstration: HMI vs. ground truth

```
   HMI/SCADA  ← Modbus ← PLCs ← bridge ← PHYSICS → direct → 3D view
   (spoofable)                                              (unspoofable)
```

A live run looks like this:

1. **Baseline operation** — the Control PLC holds the process at its setpoint, the
   Safety PLC is armed, HMI green, 3D stable. With no attacker this runs forever and
   never trips.
2. **Attack injected (external, over Modbus)** — the attacker writes a malicious
   setpoint to the **Control PLC** so its loop drives the process toward failure,
   **and** overrides the **Safety PLC**'s trip/interlock (which also freezes the data
   it feeds the HMI at nominal). The setpoint manipulation alone would be caught by
   the healthy SIS; defeating the SIS is what lets it reach failure.
3. **Divergence** — the HMI **stays green** (spoofed/frozen) while the **3D view
   shows the process running away to failure**.

That divergence — HMI nominal, ground truth in rupture — is the entire point of the
testbed. HMI read off the PLCs = spoofable; 3D read off the physics = unspoofable.

## The three damage scenarios

Each scenario maps to a real plant unit and the SIS interlock the attack disables
(Phase 1 makes those units one connected flowsheet — see below):

| Scenario              | Plant unit / failure | Interlock disabled   | Process model                                                        | Difficulty                  |
|-----------------------|----------------------|----------------------|----------------------------------------------------------------------|-----------------------------|
| **Vessel overpressure** | Pressure vessel rupture | Relief valve        | Single ODE (pressure)                                                | Easiest                     |
| **Tank overfill**       | Tank overflow / spill   | High-level trip     | Single ODE (mass balance)                                            | Safest Day-1 milestone      |
| **Reactor runaway**     | Exothermic runaway      | Cooling-jacket trip | Two coupled ODEs (temperature + concentration, Arrhenius feedback)   | Hardest — Day-2 stretch     |

The protected Phase-0 demo used **one** scenario; all three are implemented on
pure-Python ODE physics and are selectable through every layer (Phase 0.5) as
three independent single-unit sims. **Phase 1 also wires them into one
continuously-running plant** — `T-101` feed tank → `R-301` reactor → `V-201`
separation vessel, with forward-coupled streams (an overfilling tank warms the
reactor; a runaway reactor pressurises the vessel) — so each scenario becomes
**the unit the attack targets** inside the same flowsheet. This runs today on the
Python + in-memory-PLC path (`physics/plant.py`, `bridge/run_plant_demo.py`); the
whole-plant *visualization* and the live OpenPLC multi-unit stack are Phase 2.
Validated physics (OpenModelica, real geometry, relief sizing) is the **final**
phase, dropped in behind the unchanged physics interface — plausible now,
validated last. The reactor model in particular is tuned for a legible runaway,
not plant fidelity. See `ROADMAP.md`.

## Honest scope limit (read this)

PetroTwin reproduces the Triton **attack pattern**, not the Triton **mechanism**.

- The real Triton malware exploited the proprietary **TriStation** protocol
  (UDP/1502) and injected code into **Triconex** SIS controller firmware.
- OpenPLC is a **generic Modbus soft-PLC**. It cannot faithfully emulate a Triconex
  controller or the TriStation protocol.

So PetroTwin demonstrates the *shape* of the attack — manipulate the control
setpoint, compromise the SIS, defeat the interlock, spoof the operator's view, let
the process fail — using Modbus register writes rather than a Triconex-specific
exploit. Mechanism-level fidelity (emulating a
TriStation-like safety protocol) is an **explicit later-phase decision**, not an
assumed goal. Real or virtualized Triconex hardware plus original malware analysis is
out of scope entirely and would be a separate, funded project.

See `ARCHITECTURE.md` for how the layers connect, `ROADMAP.md` for the phased plan,
and `TESTING.md` for how each layer and the integrated loop are validated.
