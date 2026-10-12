(*
 * PetroTwin - Control PLC (BPCS) - canonical program for all three scenarios.
 *
 * Basic Process Control System. It runs the process under normal operation: a
 * proportional control loop that holds the measured PV at the operator's
 * SETPOINT by modulating the feed/coolant actuator. With no attacker this loop
 * simply sits at setpoint forever - that is the baseline, and the SIS never
 * needs to trip. It is NOT the safety function; the protective trip is the
 * Safety PLC's (SIS) job, applied to the physics by the Modbus bridge.
 *
 *   feed_cmd = clamp( BIAS_PM + KP_NUM*(SP_use - PV)/KP_DEN , 0 , 1000 )
 *
 * KP_NUM is SIGNED. It is positive where opening the actuator raises the PV
 * (tank feed valve, vessel compressor) and NEGATIVE for the reactor, whose
 * actuator is the coolant valve (opening it lowers temperature, so the loop
 * opens it when the PV is above setpoint). BIAS_PM/KP_NUM/KP_DEN and the
 * operator SETPOINT CLAMP (SP_LO/SP_HI) are the per-scenario part; the
 * committed defaults below are the tank scenario, and plc/load_scenario.py
 * substitutes the matching values for the others. Keep each constant line in
 * the exact form `NAME : TYPE := <n>;` so the loader can rewrite it. The values
 * come from common/scenarios.py (the single source of truth).
 *
 * REAL-SIF REALISM (this program, vs. the raw loop):
 *   - Operator SETPOINT CLAMP. A setpoint written outside [SP_LO, SP_HI] is
 *     clamped before use and SP_CLAMPED is annunciated. This defeats the naive
 *     off-scale-setpoint attack (it can no longer saturate the actuator), which
 *     is why the evolved attack drives the actuator through MANUAL mode instead.
 *   - AUTO / MANUAL mode (MAN_MODE). In MANUAL the operator (or, in the attack,
 *     the attacker) commands the actuator directly through MAN_OUT. In AUTO the
 *     program TRACKS its computed output into MAN_OUT, so AUTO->MANUAL is
 *     bumpless (MAN_OUT already equals the live output at the instant of switch).
 *   - Output RATE LIMIT. feed_cmd may move at most RATE_LIM per scan, so neither
 *     a mode switch nor an attack can step the valve instantly.
 *   - PV_HI / PV_LO process alarms (at the clamp bounds).
 *
 * THE ATTACK SURFACE. SETPOINT, MAN_MODE and MAN_OUT are writable Modbus points.
 * The naive attack writes an off-scale SETPOINT (now clamped -> caught). The
 * evolved attack flips MAN_MODE=1 and drives MAN_OUT to the dangerous extreme
 * (valve wide open / coolant shut), bypassing the control law and the clamp;
 * alone that is still caught by a healthy SIS, and the full attack also sets
 * SIS_OVERRIDE (see plc/sis/sis.st). The bridge seeds SETPOINT with the safe
 * value at startup and never rewrites it, so a malicious write persists until
 * reset. This is the Triton attack PATTERN over Modbus, not its program-
 * injection MECHANISM. See OVERVIEW.md scope limit.
 *
 * EXPORT valve. OPV_CMD (operator) -> OPV_OUT (to the P-401 product tank). It is
 * a plain operator valve with no interlock; it rides on the BPCS as generic I/O
 * and is only meaningful on the vessel unit (harmless elsewhere).
 *
 * Modbus mapping (see common/registers.py / ARCHITECTURE.md):
 *   %MW0   BPCS_PV        - true PV sensor (scaled); bridge WRITES, we only read.
 *                           A %MW MEMORY word (holding 1024), NOT %QW: a %QW output
 *                           is rewritten from our image each scan, so a master write
 *                           to it would never reach us.
 *   %MW1   BPCS_SETPOINT  - operator/ATTACK control setpoint (scaled); we only read.
 *   %MW2   BPCS_MAN_OUT   - manual actuator cmd / AUTO output-tracking (per-mille);
 *                           master writes in MANUAL, we write in AUTO (bumpless).
 *   %QW1   BPCS_FEED_CMD  - feed/coolant actuator position (per-mille); we write.
 *   %QX0.0 BPCS_MAN_MODE  - 0 = AUTO, 1 = MANUAL; operator/ATTACK writes, we read.
 *   %QX0.1 BPCS_PV_HI_ALM - 1 = PV at/above SP_HI; we write.
 *   %QX0.2 BPCS_PV_LO_ALM - 1 = PV at/below SP_LO; we write.
 *   %QX0.3 BPCS_SP_CLAMPED- 1 = operator setpoint was out of range; we write.
 *   %QX0.4 BPCS_OPV_CMD   - export-valve open request; operator writes, we read.
 *   %QX0.5 BPCS_OPV_OUT   - export-valve state (= OPV_CMD); we write.
 *)

PROGRAM prog0
  VAR
    pv         AT %MW0  : INT;    (* sensor in : true PV, scaled (read only) *)
    setpoint   AT %MW1  : INT;    (* setpoint in : operator/attack target, scaled (read only) *)
    man_out    AT %MW2  : INT;    (* manual cmd in / AUTO output-track out, per-mille *)
    feed_cmd   AT %QW1  : INT;    (* command out: actuator, per-mille (0..1000) *)
    man_mode   AT %QX0.0 : BOOL;  (* in : 0 = AUTO, 1 = MANUAL (read only) *)
    pv_hi_alm  AT %QX0.1 : BOOL;  (* out: PV >= SP_HI *)
    pv_lo_alm  AT %QX0.2 : BOOL;  (* out: PV <= SP_LO *)
    sp_clamped AT %QX0.3 : BOOL;  (* out: operator setpoint was out of range *)
    opv_cmd    AT %QX0.4 : BOOL;  (* in : export-valve open request (read only) *)
    opv_out    AT %QX0.5 : BOOL;  (* out: export-valve state (= opv_cmd) *)
  END_VAR
  VAR
    sp_use   : INT;               (* setpoint after the operator clamp *)
    target   : DINT;              (* desired output before the rate limit *)
    delta    : DINT;              (* this scan's limited output move *)
    out_prev : DINT;              (* retained across scans; rate-limited output, init 0 *)
  END_VAR
  VAR CONSTANT
    BIAS_PM  : DINT := 400;       (* actuator at zero error, per-mille (see header) *)
    KP_NUM   : DINT := 3;         (* proportional gain numerator, SIGNED (see header) *)
    KP_DEN   : DINT := 5;         (* proportional gain denominator (see header) *)
    SP_LO    : INT := 1000;       (* operator setpoint clamp low, register units *)
    SP_HI    : INT := 8000;       (* operator setpoint clamp high, register units *)
    RATE_LIM : DINT := 100;       (* max actuator move per scan, per-mille (fixed) *)
  END_VAR

  (* 1. Operator setpoint clamp (defeats the off-scale-setpoint attack). *)
  IF setpoint > SP_HI THEN
    sp_use := SP_HI;
  ELSIF setpoint < SP_LO THEN
    sp_use := SP_LO;
  ELSE
    sp_use := setpoint;
  END_IF;
  sp_clamped := (setpoint > SP_HI) OR (setpoint < SP_LO);

  (* 2. Desired output: operator's hand in MANUAL, the P-loop in AUTO.
        Computed in DINT so the gain*error product cannot overflow an INT. *)
  IF man_mode THEN
    target := INT_TO_DINT(man_out);
  ELSE
    target := BIAS_PM + (KP_NUM * INT_TO_DINT(sp_use - pv)) / KP_DEN;
  END_IF;
  IF target > 1000 THEN
    target := 1000;
  ELSIF target < 0 THEN
    target := 0;
  END_IF;

  (* 3. Bumpless transfer: in AUTO, track the live output into MAN_OUT so a later
        AUTO->MANUAL switch starts exactly where the loop left off. *)
  IF NOT man_mode THEN
    man_out := DINT_TO_INT(target);
  END_IF;

  (* 4. Output rate limit. *)
  delta := target - out_prev;
  IF delta > RATE_LIM THEN
    delta := RATE_LIM;
  ELSIF delta < (0 - RATE_LIM) THEN
    delta := 0 - RATE_LIM;
  END_IF;
  out_prev := out_prev + delta;
  IF out_prev > 1000 THEN
    out_prev := 1000;
  ELSIF out_prev < 0 THEN
    out_prev := 0;
  END_IF;
  feed_cmd := DINT_TO_INT(out_prev);

  (* 5. Process alarms + export-valve passthrough. *)
  pv_hi_alm := pv >= SP_HI;
  pv_lo_alm := pv <= SP_LO;
  opv_out   := opv_cmd;

END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : prog0;
  END_RESOURCE
END_CONFIGURATION
