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
the other. ``FakeSis`` is given the scenario's high-high trip threshold in
register units (``scenario.trip_reg``), which mirrors the ``TRIP_HH`` constant
compiled into the matching ``plc/sis/sis_<scenario>.st`` program.
"""

from __future__ import annotations

from typing import Protocol

from common import registers as R


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
class FakeBpcs:
    """In-memory Control PLC — mirrors plc/bpcs/bpcs.st (run the process)."""

    def __init__(self) -> None:
        self._holding = {int(R.BpcsHolding.PV): 0,
                         int(R.BpcsHolding.FEED_CMD): 0}

    def read_coil(self, addr: int) -> bool:
        raise KeyError(f"BPCS has no coils (addr {addr})")

    def write_coil(self, addr: int, value: bool) -> None:
        raise KeyError(f"BPCS has no coils (addr {addr})")

    def read_holding(self, addr: int) -> int:
        return self._holding[int(addr)]

    def write_holding(self, addr: int, value: int) -> None:
        self._holding[int(addr)] = int(value)

    def scan(self) -> None:
        # bpcs.st: feed_cmd := 1000  (valve fully open)
        self._holding[int(R.BpcsHolding.FEED_CMD)] = R.VALVE_REG_MAX

    def close(self) -> None:
        pass


class FakeSis:
    """In-memory Safety PLC — mirrors plc/sis/sis_<scenario>.st.

    ``trip_hh_reg`` is the high-high trip threshold in register units
    (``scenario.trip_reg``), the same constant compiled into the matching ST
    program. Logic (latched high-high trip + override force/bypass) is identical
    across scenarios; only this threshold differs.
    """

    def __init__(self, trip_hh_reg: int) -> None:
        self.trip_hh_reg = int(trip_hh_reg)
        self._coil = {int(R.SisCoil.TRIP_CMD): False,
                      int(R.SisCoil.TRIP_STATUS): False,
                      int(R.SisCoil.OVERRIDE): False}
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
        override = self._coil[int(R.SisCoil.OVERRIDE)]
        if override:
            # Compromised: suppress trip, freeze operator view.
            self._coil[int(R.SisCoil.TRIP_CMD)] = False
            self._coil[int(R.SisCoil.TRIP_STATUS)] = False
            # PV_HMI deliberately not refreshed -> holds its last value.
        else:
            if self._holding[int(R.SisHolding.PV)] >= self.trip_hh_reg:
                self._tripped = True
            self._coil[int(R.SisCoil.TRIP_CMD)] = self._tripped
            self._coil[int(R.SisCoil.TRIP_STATUS)] = self._tripped
            self._holding[int(R.SisHolding.PV_HMI)] = \
                self._holding[int(R.SisHolding.PV)]

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
