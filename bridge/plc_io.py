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
the other. ``SIS_TRIP_HH`` here mirrors ``TRIP_HH`` in ``sis.st``.
"""

from __future__ import annotations

from typing import Protocol

from common import registers as R

# High-high level trip threshold, in mm — mirrors TRIP_HH in plc/sis/sis.st.
SIS_TRIP_HH = 9000


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
    """In-memory Control PLC — mirrors plc/bpcs/bpcs.st (run the fill)."""

    def __init__(self) -> None:
        self._holding = {int(R.BpcsHolding.LEVEL_PV): 0,
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
    """In-memory Safety PLC — mirrors plc/sis/sis.st (latched trip + override)."""

    def __init__(self) -> None:
        self._coil = {int(R.SisCoil.TRIP_CMD): False,
                      int(R.SisCoil.TRIP_STATUS): False,
                      int(R.SisCoil.OVERRIDE): False}
        self._holding = {int(R.SisHolding.LEVEL_PV): 0,
                         int(R.SisHolding.LEVEL_HMI): 0}
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
            # LEVEL_HMI deliberately not refreshed -> holds its last value.
        else:
            if self._holding[int(R.SisHolding.LEVEL_PV)] >= SIS_TRIP_HH:
                self._tripped = True
            self._coil[int(R.SisCoil.TRIP_CMD)] = self._tripped
            self._coil[int(R.SisCoil.TRIP_STATUS)] = self._tripped
            self._holding[int(R.SisHolding.LEVEL_HMI)] = \
                self._holding[int(R.SisHolding.LEVEL_PV)]

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
        from pymodbus.client import ModbusTcpClient  # lazy import

        self._unit = unit
        self._client = ModbusTcpClient(host, port=port, timeout=timeout)
        if not self._client.connect():
            raise ConnectionError(f"could not connect to OpenPLC at {host}:{port}")

    @classmethod
    def for_instance(cls, instance: R.Instance, **kw) -> "ModbusPlc":
        host, port = R.ENDPOINTS[instance]
        return cls(host, port, **kw)

    def read_coil(self, addr: int) -> bool:
        rr = self._client.read_coils(int(addr), count=1, slave=self._unit)
        if rr.isError():
            raise IOError(f"read_coil {addr} failed: {rr}")
        return bool(rr.bits[0])

    def write_coil(self, addr: int, value: bool) -> None:
        rr = self._client.write_coil(int(addr), bool(value), slave=self._unit)
        if rr.isError():
            raise IOError(f"write_coil {addr} failed: {rr}")

    def read_holding(self, addr: int) -> int:
        rr = self._client.read_holding_registers(int(addr), count=1, slave=self._unit)
        if rr.isError():
            raise IOError(f"read_holding {addr} failed: {rr}")
        return int(rr.registers[0])

    def write_holding(self, addr: int, value: int) -> None:
        rr = self._client.write_register(int(addr), int(value), slave=self._unit)
        if rr.isError():
            raise IOError(f"write_holding {addr} failed: {rr}")

    def scan(self) -> None:
        # The real PLC scans on its own task; nothing to do here.
        pass

    def close(self) -> None:
        self._client.close()
