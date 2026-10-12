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
 *   feed_cmd = clamp( BIAS_PM + KP_NUM*(SETPOINT - PV)/KP_DEN , 0 , 1000 )
 *
 * KP_NUM is SIGNED. It is positive where opening the actuator raises the PV
 * (tank feed valve, vessel compressor) and NEGATIVE for the reactor, whose
 * actuator is the coolant valve (opening it lowers temperature, so the loop
 * opens it when the PV is above setpoint). BIAS_PM/KP_NUM/KP_DEN are the ONE
 * per-scenario part; the committed defaults below are the tank scenario, and
 * plc/load_scenario.py substitutes the matching values for the others. Keep
 * each line in the exact form `NAME : DINT := <n>;` so the loader can rewrite
 * it. The gains come from common/scenarios.py (the single source of truth).
 *
 * THE SETPOINT IS AN ATTACK SURFACE. It is a writable Modbus register, so an
 * external attacker can write a malicious setpoint over the network and make
 * this loop drive the process toward failure (feed valves wide open / the
 * coolant valve shut). That alone is caught by a healthy SIS; the full attack
 * also sets SIS_OVERRIDE (see plc/sis/sis.st). The bridge seeds SETPOINT with
 * the safe value at startup and never rewrites it, so a malicious write
 * persists until reset. This is the Triton attack PATTERN (manipulate the
 * control setpoint + defeat the safety layer over Modbus), not its
 * program-injection MECHANISM. See OVERVIEW.md scope limit.
 *
 * Modbus mapping (see common/registers.py / ARCHITECTURE.md):
 *   %MW0  BPCS_PV        - true PV sensor (scaled); bridge WRITES, we only read.
 *                          A %MW MEMORY word (holding 1024), NOT %QW: a %QW output
 *                          is rewritten from our image each scan, so a master write
 *                          to it would never reach us.
 *   %MW1  BPCS_SETPOINT  - operator/ATTACK control setpoint (scaled); we only read.
 *                          Also a %MW word so a master (operator or attacker) write
 *                          reaches us.
 *   %QW1  BPCS_FEED_CMD  - feed/coolant actuator position (per-mille); we write.
 *)

PROGRAM prog0
  VAR
    pv       AT %MW0 : INT;   (* sensor in : true PV, scaled (read only) *)
    setpoint AT %MW1 : INT;   (* setpoint in : operator/attack target, scaled (read only) *)
    feed_cmd AT %QW1 : INT;   (* command out: actuator, per-mille (0..1000) *)
  END_VAR
  VAR
    raw : DINT;               (* control output before clamping *)
  END_VAR
  VAR CONSTANT
    BIAS_PM : DINT := 400;    (* actuator at zero error, per-mille (see header) *)
    KP_NUM  : DINT := 3;      (* proportional gain numerator, SIGNED (see header) *)
    KP_DEN  : DINT := 5;      (* proportional gain denominator (see header) *)
  END_VAR

  (* Proportional loop: drive the actuator to hold PV at the setpoint.
     Computed in DINT so the gain*error product cannot overflow an INT. *)
  raw := BIAS_PM + (KP_NUM * INT_TO_DINT(setpoint - pv)) / KP_DEN;
  IF raw > 1000 THEN
    raw := 1000;
  ELSIF raw < 0 THEN
    raw := 0;
  END_IF;
  feed_cmd := DINT_TO_INT(raw);

END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : prog0;
  END_RESOURCE
END_CONFIGURATION
