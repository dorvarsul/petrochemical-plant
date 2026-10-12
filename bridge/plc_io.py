"""PLC I/O clients used by the bridge.

Two implementations behind one small interface:

* ``FakeBpcs`` / ``FakeSis`` — pure-Python in-memory PLCs that mirror the exact
  logic of ``plc/bpcs/bpcs.st`` and ``plc/sis/sis.st``. They let the whole
  integrated loop (normal → attack → overflow) be tested deterministically
  without Docker or OpenPLC, which is how the Phase-0 demo logic is de-risked
  before the fragile live integration.
* ``ModbusPlc`` — a thin pymodbus client talking to a real OpenPLC v3 slave.
  Same interface, so the bridge code is identical for fake and real PLCs.

The fake PLCs and the ST programs must stay in lock-step: if one changes, change
the other. Both ``FakeBpcs`` and ``FakeSis`` take the whole ``scenario`` (the
single source of truth) and derive the exact constants the loader substitutes
into the matching ST programs (control gains + setpoint clamp for the BPCS;
trip / pre-alarm / reset-deadband / full-scale for the SIS).
"""

from __future__ import annotations

from typing import Protocol

from common import registers as R
from common import scenarios


class PlcClient(Protocol):
    """Minimal Modbus-style access one PLC instance exposes to the bridge."""

    def read_coil(self, addr: int) -> bool: ...
    def write_coil(self, addr: int, value: bool) -> None: ...
    def read_holding(self, addr: int) -> int: ...
    def write_holding(self, addr: int, value: int) -> None: ...
    def scan(self) -> None:
        """Advance the PLC's internal logic by one scan (fake PLCs only)."""
        ...
    def close(self) -> None: ...


# --------------------------------------------------------------------------
# Fake PLCs (mirror the ST programs)
# --------------------------------------------------------------------------
def _idiv(a: int, b: int) -> int:
    """Integer division truncated toward zero, matching IEC-61131-3 / the ST
    program (Python's // truncates toward -inf, which would disagree by 1 LSB
    for a negative control term)."""
    return int(a / b)


class FakeBpcs:
    """In-memory Control PLC — mirrors plc/bpcs/bpcs.st (setpoint control).

    Byte-for-byte logical twin of the ST program, using the scenario's control
    constants (``ctrl_bias_pm`` / ``ctrl_kp_num`` / ``ctrl_kp_den``) and the
    operator setpoint clamp (``sp_lo_reg`` / ``sp_hi_reg``) — the same single
    source of truth the loader substitutes into the real PLC. It implements:

      * operator SETPOINT clamp to [sp_lo, sp_hi] (+ SP_CLAMPED annunciation),
      * AUTO / MANUAL mode (MAN_MODE) with bumpless transfer (AUTO tracks the
        live output into MAN_OUT), the evolved attack's new driver,
      * the proportional law in AUTO:
            target = clamp(bias_pm + kp_num*(sp_use - PV)//kp_den, 0, 1000)
      * an output RATE LIMIT (``scenarios.CTRL_RATE_LIM_PM`` per scan),
      * PV_HI / PV_LO process alarms and the export-valve passthrough.

    SETPOINT / MAN_OUT are master-writable %MW words; MAN_MODE / OPV_CMD are
    program-read coils the master writes — exactly like the real registers.
    """

    def __init__(self, scenario) -> None:
        self.scenario = scenario
        self._holding = {int(R.BpcsHolding.PV): 0,
                         int(R.BpcsHolding.SETPOINT): 0,
                         int(R.BpcsHolding.MAN_OUT): 0,
                         int(R.BpcsHolding.FEED_CMD): 0}
        self._coil = {int(R.BpcsCoil.MAN_MODE): False,
                      int(R.BpcsCoil.PV_HI_ALARM): False,
                      int(R.BpcsCoil.PV_LO_ALARM): False,
                      int(R.BpcsCoil.SP_CLAMPED): False,
                      int(R.BpcsCoil.OPV_CMD): False,
                      int(R.BpcsCoil.OPV_OUT): False}
        self._out_prev = 0  # retained rate-limited output (ST VAR out_prev)

    def read_coil(self, addr: int) -> bool:
        return self._coil[int(addr)]

    def write_coil(self, addr: int, value: bool) -> None:
        self._coil[int(addr)] = bool(value)

    def read_holding(self, addr: int) -> int:
        return self._holding[int(addr)]

    def write_holding(self, addr: int, value: int) -> None:
        self._holding[int(addr)] = int(value)

    def scan(self) -> None:
        sc = self.scenario
        H, C = self._holding, self._coil
        pv = H[int(R.BpcsHolding.PV)]
        sp = H[int(R.BpcsHolding.SETPOINT)]
        man_out = H[int(R.BpcsHolding.MAN_OUT)]
        man = C[int(R.BpcsCoil.MAN_MODE)]
        lo, hi = sc.sp_lo_reg, sc.sp_hi_reg

        # 1. operator setpoint clamp
        sp_use = hi if sp > hi else lo if sp < lo else sp
        C[int(R.BpcsCoil.SP_CLAMPED)] = sp_use != sp

        # 2. desired output: operator's hand in MANUAL, P-loop in AUTO
        if man:
            target = man_out
        else:
            target = sc.ctrl_bias_pm + _idiv(
                sc.ctrl_kp_num * (sp_use - pv), sc.ctrl_kp_den)
        target = 0 if target < 0 else R.VALVE_REG_MAX if target > R.VALVE_REG_MAX else target

        # 3. bumpless transfer: track the live output into MAN_OUT while in AUTO
        if not man:
            H[int(R.BpcsHolding.MAN_OUT)] = target

        # 4. output rate limit
        rate = scenarios.CTRL_RATE_LIM_PM
        delta = target - self._out_prev
        delta = rate if delta > rate else -rate if delta < -rate else delta
        out = self._out_prev + delta
        out = 0 if out < 0 else R.VALVE_REG_MAX if out > R.VALVE_REG_MAX else out
        self._out_prev = out
        H[int(R.BpcsHolding.FEED_CMD)] = out

        # 5. process alarms + export-valve passthrough
        C[int(R.BpcsCoil.PV_HI_ALARM)] = pv >= hi
        C[int(R.BpcsCoil.PV_LO_ALARM)] = pv <= lo
        C[int(R.BpcsCoil.OPV_OUT)] = C[int(R.BpcsCoil.OPV_CMD)]

    def close(self) -> None:
        pass


class FakeSis:
    """In-memory Safety PLC — mirrors plc/sis/sis.st.

    Byte-for-byte logical twin of the ST program. Takes the whole ``scenario``
    (the single source of truth) and derives the trip / pre-alarm / reset-
    deadband / full-scale thresholds the matching ST constants are substituted
    from. Implements the latched high-high trip, the maintenance BYPASS
    (``override``) that suppresses the trip and freezes the HMI, the pre-alarm
    "H", a bad-PV fail-safe trip, a deadband-gated manual reset, and the
    block-valve final element (``blk_out = blk_open_cmd AND NOT trip``).
    """

    def __init__(self, scenario) -> None:
        self.scenario = scenario
        self.trip_hh_reg = int(scenario.trip_reg)
        self._coil = {int(R.SisCoil.TRIP_CMD): False,
                      int(R.SisCoil.TRIP_STATUS): False,
                      int(R.SisCoil.OVERRIDE): False,
                      int(R.SisCoil.RESET): False,
                      int(R.SisCoil.PREALARM_H): False,
                      int(R.SisCoil.PV_BAD): False,
                      int(R.SisCoil.BLK_OPEN_CMD): False,
                      int(R.SisCoil.BLK_OUT): False,
                      int(R.SisCoil.BYPASS_ACTIVE): False}
        self._holding = {int(R.SisHolding.PV): 0,
                         int(R.SisHolding.PV_HMI): 0}
        self._tripped = False  # latched trip state

    def read_coil(self, addr: int) -> bool:
        return self._coil[int(addr)]

    def write_coil(self, addr: int, value: bool) -> None:
        self._coil[int(addr)] = bool(value)

    def read_holding(self, addr: int) -> int:
        return self._holding[int(addr)]

    def write_holding(self, addr: int, value: int) -> None:
        self._holding[int(addr)] = int(value)

    def scan(self) -> None:
        sc = self.scenario
        H, C = self._holding, self._coil
        pv = H[int(R.SisHolding.PV)]
        tr = self.trip_hh_reg

        # transmitter validity + pre-alarm (both gated on a good PV)
        bad = (pv < 0) or (pv > sc.reg_full_scale)   # PV_MIN=0, PV_MAX=full scale
        C[int(R.SisCoil.PV_BAD)] = bad
        C[int(R.SisCoil.PREALARM_H)] = (not bad) and pv >= sc.pre_alarm_h_reg
        C[int(R.SisCoil.BYPASS_ACTIVE)] = C[int(R.SisCoil.OVERRIDE)]

        if C[int(R.SisCoil.OVERRIDE)]:
            # Bypass (abused): suppress trip, freeze operator view.
            C[int(R.SisCoil.TRIP_CMD)] = False
            C[int(R.SisCoil.TRIP_STATUS)] = False
            # PV_HMI deliberately not refreshed -> holds its last value.
        else:
            if pv >= tr or bad:                       # fail-safe on bad PV
                self._tripped = True
            if (self._tripped and C[int(R.SisCoil.RESET)] and (not bad)
                    and pv < tr - sc.reset_db_reg):   # deadband-gated reset
                self._tripped = False
            C[int(R.SisCoil.TRIP_CMD)] = self._tripped
            C[int(R.SisCoil.TRIP_STATUS)] = self._tripped
            H[int(R.SisHolding.PV_HMI)] = pv

        # block-valve final element: operator opens it, the trip shuts it
        C[int(R.SisCoil.BLK_OUT)] = (
            C[int(R.SisCoil.BLK_OPEN_CMD)] and (not C[int(R.SisCoil.TRIP_CMD)]))

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------
# Real OpenPLC client (pymodbus) — exercised in the live step, not unit-tested
# --------------------------------------------------------------------------
class ModbusPlc:
    """pymodbus-backed client for a real OpenPLC v3 Modbus slave.

    pymodbus is imported lazily so the dependency is only needed for the live
    bridge, not for the fake-PLC unit tests. Verified against a running OpenPLC
    in the live step; API targets pymodbus 3.x.
    """

    def __init__(self, host: str, port: int, unit: int = 1, timeout: float = 1.0) -> None:
        import inspect
        from pymodbus.client import ModbusTcpClient  # lazy import

        self._unit = unit
        self._client = ModbusTcpClient(host, port=port, timeout=timeout)
        if not self._client.connect():
            raise ConnectionError(f"could not connect to OpenPLC at {host}:{port}")
        # pymodbus renamed the unit-id kwarg from `slave` to `device_id` mid-3.x.
        params = inspect.signature(self._client.read_coils).parameters
        self._id_kw = "device_id" if "device_id" in params else "slave"

    def _id(self) -> dict:
        return {self._id_kw: self._unit}

    @classmethod
    def for_instance(cls, instance: R.Instance, **kw) -> "ModbusPlc":
        host, port = R.ENDPOINTS[instance]
        return cls(host, port, **kw)

    def read_coil(self, addr: int) -> bool:
        rr = self._client.read_coils(int(addr), count=1, **self._id())
        if rr.isError():
            raise IOError(f"read_coil {addr} failed: {rr}")
        return bool(rr.bits[0])

    def write_coil(self, addr: int, value: bool) -> None:
        rr = self._client.write_coil(int(addr), bool(value), **self._id())
        if rr.isError():
            raise IOError(f"write_coil {addr} failed: {rr}")

    def read_holding(self, addr: int) -> int:
        rr = self._client.read_holding_registers(int(addr), count=1, **self._id())
        if rr.isError():
            raise IOError(f"read_holding {addr} failed: {rr}")
        return int(rr.registers[0])

    def write_holding(self, addr: int, value: int) -> None:
        rr = self._client.write_register(int(addr), int(value), **self._id())
        if rr.isError():
            raise IOError(f"write_holding {addr} failed: {rr}")

    def scan(self) -> None:
        # The real PLC scans on its own task; nothing to do here.
        pass

    def close(self) -> None:
        self._client.close()


class ModbusUnitView:
    """A :class:`PlcClient` for ONE unit sharing a real OpenPLC slave.

    The connected whole-plant view runs three units on two slaves (one BPCS, one
    SIS). Each unit's registers live in its own offset block within the shared
    slave, so this wrapper holds the shared :class:`ModbusPlc` plus the unit's
    index and projects every address through :func:`registers.unit_addr` — the
    same offset the generated multi-unit ST program uses. ``PlantBridge`` is thus
    unchanged: it still passes the bare single-unit enum addresses per unit.

    Three views (tank/reactor/vessel) share one underlying client, so ``close``
    is idempotent (pymodbus ``close`` tolerates repeat calls).
    """

    def __init__(self, plc: ModbusPlc, unit_index: int) -> None:
        self._plc = plc
        self._u = int(unit_index)

    def read_coil(self, addr: int) -> bool:
        return self._plc.read_coil(R.unit_addr("coil", int(addr), self._u))

    def write_coil(self, addr: int, value: bool) -> None:
        self._plc.write_coil(R.unit_addr("coil", int(addr), self._u), bool(value))

    def read_holding(self, addr: int) -> int:
        return self._plc.read_holding(R.unit_addr("holding", int(addr), self._u))

    def write_holding(self, addr: int, value: int) -> None:
        self._plc.write_holding(R.unit_addr("holding", int(addr), self._u), int(value))

    def scan(self) -> None:
        pass  # the real PLC scans autonomously

    def close(self) -> None:
        self._plc.close()
