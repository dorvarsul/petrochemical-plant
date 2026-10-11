(*
 * PetroTwin - Safety PLC (SIS) - Phase 0, tank-overfill scenario.
 *
 * Safety Instrumented System. Independent of the BPCS. Holds the high-level
 * trip/interlock: when the true level reaches the high-high threshold it asserts
 * the trip (force the feed shut) and latches. It also mirrors the level to the
 * operator HMI and reports its armed/tripped status.
 *
 * This is the ATTACK TARGET. The attacker sets SIS_OVERRIDE over Modbus, which
 * puts the logic into a force/bypass state: the trip is suppressed and the HMI
 * display stops refreshing (freezes at its last nominal value). That abuses a
 * force path reachable over Modbus - the Triton attack PATTERN, not its
 * program-injection MECHANISM (Phase 3). See OVERVIEW.md scope limit.
 *
 * Modbus mapping (see common/registers.py / ARCHITECTURE.md):
 *   %QW0   SIS_LEVEL_PV    - true level (mm); bridge WRITES, we only read
 *   %QW1   SIS_LEVEL_HMI   - displayed level (mm); we write (held under override)
 *   %QX0.0 SIS_TRIP_CMD    - 1 = force feed shut; we write, bridge reads
 *   %QX0.1 SIS_TRIP_STATUS - 1 = tripped, 0 = armed-normal; we write, HMI reads
 *   %QX0.2 SIS_OVERRIDE    - 1 = force/bypass; attacker WRITES, we only read
 *)

PROGRAM prog0
  VAR
    level_pv    AT %QW0  : INT;    (* sensor in : true level, mm (read only) *)
    level_hmi   AT %QW1  : INT;    (* HMI display: level, mm (held under override) *)
    trip_cmd    AT %QX0.0 : BOOL;  (* out: force feed shut *)
    trip_status AT %QX0.1 : BOOL;  (* out: 1 = tripped, 0 = armed-normal *)
    override    AT %QX0.2 : BOOL;  (* in : 1 = force/bypass (read only) *)
  END_VAR
  VAR
    tripped     : BOOL;            (* latched trip state (internal) *)
  END_VAR
  VAR CONSTANT
    TRIP_HH : INT := 9000;         (* high-high level trip = 9.0 m, in mm *)
  END_VAR

  IF override THEN
    (* Compromised: suppress protection and freeze the operator's view. *)
    trip_cmd    := FALSE;
    trip_status := FALSE;          (* indicator held at "armed-normal" *)
    (* level_hmi deliberately NOT updated -> holds its last nominal value *)
  ELSE
    (* Healthy SIS: latch the high-level trip, mirror true level to the HMI. *)
    IF level_pv >= TRIP_HH THEN
      tripped := TRUE;
    END_IF;
    trip_cmd    := tripped;
    trip_status := tripped;
    level_hmi   := level_pv;
  END_IF;

END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : prog0;
  END_RESOURCE
END_CONFIGURATION
