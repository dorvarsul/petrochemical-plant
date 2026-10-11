(*
 * PetroTwin - Control PLC (BPCS) - shared across all scenarios.
 *
 * Basic Process Control System. Its job is simply to run the process: hold the
 * feed actuator fully open (fill valve / compressor feed / reactant feed,
 * depending on the scenario). It is NOT the safety function and performs no
 * trip - the protective action is the Safety PLC's (SIS) job, applied to the
 * physics by the Modbus bridge.
 *
 * The same program serves every scenario because the BPCS behaviour is
 * identical (drive the process); only the SIS trip threshold differs per
 * scenario (see plc/sis/sis.st + plc/load_scenario.py).
 *
 * Modbus mapping (see common/registers.py / ARCHITECTURE.md):
 *   %MW0  BPCS_PV        - true PV sensor (scaled); bridge WRITES, we only read.
 *                          A %MW MEMORY word (holding 1024), NOT %QW: a %QW output
 *                          is rewritten from our image each scan, so a master write
 *                          to it would never reach us.
 *   %QW1  BPCS_FEED_CMD  - feed actuator position (per-mille); we write, bridge reads
 *)

PROGRAM prog0
  VAR
    pv       AT %MW0 : INT;   (* sensor in : true PV, scaled (read only) *)
    feed_cmd AT %QW1 : INT;   (* command out: feed actuator, per-mille (0..1000) *)
  END_VAR

  (* Run the process - feed actuator fully open. *)
  feed_cmd := 1000;

END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : prog0;
  END_RESOURCE
END_CONFIGURATION
