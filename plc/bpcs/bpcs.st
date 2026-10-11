(*
 * PetroTwin - Control PLC (BPCS) - Phase 0, tank-overfill scenario.
 *
 * Basic Process Control System. In Phase 0 its job is simply to run the fill:
 * hold the feed valve fully open. It is NOT the safety function and performs no
 * trip - stopping the fill at high level is the Safety PLC's (SIS) job, applied
 * to the physics by the Modbus bridge as:
 *     effective_feed = 0 if SIS_TRIP_CMD else BPCS_FEED_CMD
 *
 * Modbus mapping (see common/registers.py / ARCHITECTURE.md):
 *   %QW0  BPCS_LEVEL_PV  - true level (mm); bridge WRITES, we only read
 *   %QW1  BPCS_FEED_CMD  - fill-valve position (per-mille); we write, bridge reads
 *)

PROGRAM prog0
  VAR
    level_pv AT %QW0 : INT;   (* sensor in : true level, mm (read only) *)
    feed_cmd AT %QW1 : INT;   (* command out: fill valve, per-mille (0..1000) *)
  END_VAR

  (* Phase 0: run the fill - valve fully open. *)
  feed_cmd := 1000;

END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : prog0;
  END_RESOURCE
END_CONFIGURATION
