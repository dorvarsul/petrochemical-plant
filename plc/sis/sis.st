(*
 * PetroTwin - Safety PLC (SIS) - canonical program for all three scenarios.
 *
 * Safety Instrumented System. Independent of the BPCS. Holds the high-high
 * trip/interlock: when the true PV reaches the high-high threshold it asserts
 * the trip (force the protective action) and latches. It also mirrors the PV to
 * the operator HMI and reports its armed/tripped status. The protective action
 * is scenario-specific in the PHYSICS (feed shut / relief open / max cooling),
 * but the SIS logic here is identical for every scenario - only the TRIP_HH
 * constant differs.
 *
 * TRIP_HH is the ONE scenario-specific value. It is a register-unit integer and
 * its single source of truth is `scenario.trip_reg` in common/scenarios.py.
 * The committed default below is the tank scenario (9000 = 9.0 m); for the
 * other scenarios, plc/load_scenario.py substitutes the matching value before
 * loading (vessel 1600 = 16.0 bar, reactor 1100 = 110.0 degC). Keep this line
 * in the exact form `TRIP_HH : INT := <n>;` so the loader can rewrite it.
 *
 * This is the ATTACK TARGET. The attacker sets SIS_OVERRIDE over Modbus, which
 * puts the logic into a force/bypass state: the trip is suppressed and the HMI
 * display stops refreshing (freezes at its last nominal value). That abuses a
 * force path reachable over Modbus - the Triton attack PATTERN, not its
 * program-injection MECHANISM (Phase 3). See OVERVIEW.md scope limit.
 *
 * Modbus mapping (see common/registers.py / ARCHITECTURE.md):
 *   %MW0   SIS_PV         - true PV sensor (scaled); bridge WRITES, we only read.
 *                           A %MW MEMORY word (holding 1024), NOT %QW: a %QW output
 *                           is rewritten from our image each scan, so a master write
 *                           to it would never reach us.
 *   %QW1   SIS_PV_HMI     - displayed PV (scaled); we write (held under override)
 *   %QX0.0 SIS_TRIP_CMD   - 1 = force protective action; we write, bridge reads
 *   %QX0.1 SIS_TRIP_STATUS- 1 = tripped, 0 = armed-normal; we write, HMI reads
 *   %QX0.2 SIS_OVERRIDE   - 1 = force/bypass; attacker WRITES, we only read
 *)

PROGRAM prog0
  VAR
    pv          AT %MW0  : INT;    (* sensor in : true PV, scaled (read only) *)
    pv_hmi      AT %QW1  : INT;    (* HMI display: PV, scaled (held under override) *)
    trip_cmd    AT %QX0.0 : BOOL;  (* out: force protective action *)
    trip_status AT %QX0.1 : BOOL;  (* out: 1 = tripped, 0 = armed-normal *)
    override    AT %QX0.2 : BOOL;  (* in : 1 = force/bypass (read only) *)
  END_VAR
  VAR
    tripped     : BOOL;            (* latched trip state (internal) *)
  END_VAR
  VAR CONSTANT
    TRIP_HH : INT := 9000;         (* high-high trip, register units (see header) *)
  END_VAR

  IF override THEN
    (* Compromised: suppress protection and freeze the operator's view. *)
    trip_cmd    := FALSE;
    trip_status := FALSE;          (* indicator held at "armed-normal" *)
    (* pv_hmi deliberately NOT updated -> holds its last nominal value *)
  ELSE
    (* Healthy SIS: latch the high-high trip, mirror true PV to the HMI. *)
    IF pv >= TRIP_HH THEN
      tripped := TRUE;
    END_IF;
    trip_cmd    := tripped;
    trip_status := tripped;
    pv_hmi      := pv;
  END_IF;

END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : prog0;
  END_RESOURCE
END_CONFIGURATION
