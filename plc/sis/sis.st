(*
 * PetroTwin - Safety PLC (SIS) - canonical program for all three scenarios.
 *
 * Safety Instrumented System. Independent of the BPCS. Holds the high-high
 * trip/interlock: when the true PV reaches the high-high threshold it asserts
 * the trip (force the protective action) and latches. It also mirrors the PV to
 * the operator HMI and reports its armed/tripped status. The protective action
 * is scenario-specific in the PHYSICS (feed shut / relief open / max cooling),
 * but the SIS logic here is identical for every scenario - only the trip-level
 * constants differ.
 *
 * REAL-SIF REALISM (this program, vs. the raw one-way latch):
 *   - PRE-ALARM "H". prealarm_h asserts at PRE_ALARM_H, below the "HH" trip, so
 *     the operator sees the approach before the trip fires.
 *   - MANUAL RESET with DEADBAND. A latched trip clears only on a reset
 *     pushbutton (reset_pb) AND once the PV has fallen a deadband below the trip
 *     (pv < TRIP_HH - RESET_DB) - never while the hazard is still present.
 *   - BAD-PV / out-of-range detect. pv_bad asserts when the transmitter reads
 *     outside [PV_MIN, PV_MAX]; a bad PV forces the trip (fail-safe) and blocks
 *     both the pre-alarm and the reset.
 *   - BLOCK-VALVE final element. blk_out = blk_open_cmd AND NOT trip_cmd: the
 *     operator opens the block/fill valve, and the trip physically shuts it.
 *   - MAINTENANCE BYPASS (MOS). override is reframed as a maintenance bypass;
 *     bypass_active annunciates it (the one honest "tell" during the attack).
 *
 * The per-scenario values are TRIP_HH / PRE_ALARM_H / RESET_DB / PV_MAX, whose
 * single source of truth is common/scenarios.py (trip_reg / pre_alarm_h_reg /
 * reset_db_reg / reg_full_scale). The committed defaults below are the tank
 * scenario; plc/load_scenario.py substitutes the matching values before loading.
 * Keep each line in the exact form `NAME : INT := <n>;` so the loader can
 * rewrite it. PV_MIN is fixed (0) and not substituted.
 *
 * This is the ATTACK TARGET. The attacker sets override (the maintenance BYPASS)
 * over Modbus, which suppresses the trip and freezes the HMI display at its last
 * nominal value - the Triton attack PATTERN, not its program-injection MECHANISM
 * (Phase 3). See OVERVIEW.md scope limit.
 *
 * Modbus mapping (see common/registers.py / ARCHITECTURE.md):
 *   %MW0   SIS_PV         - true PV sensor (scaled); bridge WRITES, we only read.
 *                           A %MW MEMORY word (holding 1024), NOT %QW.
 *   %QW1   SIS_PV_HMI     - displayed PV (scaled); we write (held under bypass)
 *   %QX0.0 SIS_TRIP_CMD   - 1 = force protective action; we write, bridge reads
 *   %QX0.1 SIS_TRIP_STATUS- 1 = tripped, 0 = armed-normal; we write, HMI reads
 *   %QX0.2 SIS_OVERRIDE   - 1 = maintenance BYPASS/MOS; attacker WRITES, we read
 *   %QX0.3 SIS_RESET      - manual reset pushbutton; operator WRITES, we read
 *   %QX0.4 SIS_PREALARM_H - 1 = PV at/above PRE_ALARM_H; we write
 *   %QX0.5 SIS_PV_BAD     - 1 = transmitter out of range; we write
 *   %QX0.6 SIS_BLK_OPEN   - block/fill-valve open request; operator WRITES, we read
 *   %QX0.7 SIS_BLK_OUT    - block-valve state (= cmd AND NOT trip); we write
 *   %QX1.0 SIS_BYPASS_ACT - 1 = bypass active (MOS annunciation); we write
 *)

PROGRAM prog0
  VAR
    pv            AT %MW0  : INT;    (* sensor in : true PV, scaled (read only) *)
    pv_hmi        AT %QW1  : INT;    (* HMI display: PV, scaled (held under bypass) *)
    trip_cmd      AT %QX0.0 : BOOL;  (* out: force protective action *)
    trip_status   AT %QX0.1 : BOOL;  (* out: 1 = tripped, 0 = armed-normal *)
    override      AT %QX0.2 : BOOL;  (* in : 1 = maintenance BYPASS/MOS (read only) *)
    reset_pb      AT %QX0.3 : BOOL;  (* in : manual reset pushbutton (read only) *)
    prealarm_h    AT %QX0.4 : BOOL;  (* out: PV >= PRE_ALARM_H *)
    pv_bad        AT %QX0.5 : BOOL;  (* out: transmitter out of range *)
    blk_open_cmd  AT %QX0.6 : BOOL;  (* in : block/fill-valve open request (read only) *)
    blk_out       AT %QX0.7 : BOOL;  (* out: block valve = cmd AND NOT trip *)
    bypass_active AT %QX1.0 : BOOL;  (* out: bypass active (MOS annunciation) *)
  END_VAR
  VAR
    tripped : BOOL;                  (* latched trip state (internal) *)
  END_VAR
  VAR CONSTANT
    TRIP_HH     : INT := 9000;       (* high-high trip, register units (see header) *)
    PRE_ALARM_H : INT := 8500;       (* pre-alarm "H", below the trip *)
    RESET_DB    : INT := 500;        (* reset deadband below the trip *)
    PV_MAX      : INT := 10000;      (* transmitter upper range (= reg full scale) *)
    PV_MIN      : INT := 0;          (* transmitter lower range (fixed) *)
  END_VAR

  (* Transmitter validity + pre-alarm (both gated on a good PV). *)
  pv_bad := (pv < PV_MIN) OR (pv > PV_MAX);
  IF pv_bad THEN
    prealarm_h := FALSE;
  ELSE
    prealarm_h := pv >= PRE_ALARM_H;
  END_IF;
  bypass_active := override;

  IF override THEN
    (* Maintenance bypass (abused by the attack): suppress protection and freeze
       the operator's view. *)
    trip_cmd    := FALSE;
    trip_status := FALSE;          (* indicator held at "armed-normal" *)
    (* pv_hmi deliberately NOT updated -> holds its last nominal value *)
  ELSE
    (* Healthy SIS: latch the high-high trip (or a bad PV, fail-safe), allow a
       deadband-gated manual reset, mirror true PV to the HMI. *)
    IF (pv >= TRIP_HH) OR pv_bad THEN
      tripped := TRUE;
    END_IF;
    IF tripped AND reset_pb AND (NOT pv_bad) AND (pv < (TRIP_HH - RESET_DB)) THEN
      tripped := FALSE;
    END_IF;
    trip_cmd    := tripped;
    trip_status := tripped;
    pv_hmi      := pv;
  END_IF;

  (* Block-valve final element: the operator opens it; the trip shuts it. *)
  blk_out := blk_open_cmd AND (NOT trip_cmd);

END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : prog0;
  END_RESOURCE
END_CONFIGURATION
