"""The Phase-0 Modbus bridge loop.

Couples the tank physics to the Control PLC (BPCS) and Safety PLC (SIS). Each
cycle:

  1. publish the true level to both PLCs as a sensor reading (holding %QW0),
  2. let the PLCs scan,
  3. read the fill-valve command (BPCS) and the trip command (SIS),
  4. compute the effective feed = 0 if SIS trip else BPCS command,
  5. integrate the physics one step.

The 3D ground-truth view reads ``bridge.tank`` directly (never these Modbus
values) — that direct tap is what makes it unspoofable. The HMI values the
operator would see are exposed via ``hmi_level`` / ``hmi_trip_status``, which
read them back from the (possibly compromised) SIS.
"""

from __future__ import annotations

from dataclasses import dataclass

from common import registers as R
from bridge.plc_io import PlcClient
from physics.tank import TankModel


@dataclass
class BridgeSample:
    """One cycle's record — the ground truth vs. what the operator sees."""
    time: float
    true_level: float       # m, from the physics (ground truth / 3D)
    feed: float             # effective valve fraction applied to the physics
    trip_cmd: bool          # SIS trip asserted this cycle
    hmi_level: float        # m, as shown on the operator HMI (SIS-reported)
    hmi_trip_status: bool   # operator indicator: True = tripped, False = armed

    @property
    def divergence(self) -> float:
        """|true level − HMI level| (m) — the spoof gap (acceptance crit. F)."""
        return abs(self.true_level - self.hmi_level)


class Bridge:
    def __init__(self, tank: TankModel, bpcs: PlcClient, sis: PlcClient,
                 dt: float = 0.1) -> None:
        self.tank = tank
        self.bpcs = bpcs
        self.sis = sis
        self.dt = dt

    def step(self) -> BridgeSample:
        # 1. publish true level to both PLCs (sensor injection into %QW0)
        lvl_reg = R.level_to_reg(self.tank.level)
        self.bpcs.write_holding(R.BpcsHolding.LEVEL_PV, lvl_reg)
        self.sis.write_holding(R.SisHolding.LEVEL_PV, lvl_reg)

        # 2. let the PLCs run their logic (no-op for a real OpenPLC)
        self.bpcs.scan()
        self.sis.scan()

        # 3. read actuator command and trip
        feed_permille = self.bpcs.read_holding(R.BpcsHolding.FEED_CMD)
        trip = self.sis.read_coil(R.SisCoil.TRIP_CMD)

        # 4. effective feed: the SIS trip forces the feed shut
        feed = 0.0 if trip else R.reg_to_valve(feed_permille)

        # 5. integrate physics one step
        self.tank.step(self.dt, feed)

        return BridgeSample(
            time=self.tank.time,
            true_level=self.tank.level,
            feed=feed,
            trip_cmd=trip,
            hmi_level=self.hmi_level(),
            hmi_trip_status=self.hmi_trip_status(),
        )

    # --- Operator-facing (HMI) reads, off the SIS registers ---
    def hmi_level(self) -> float:
        return R.reg_to_level(self.sis.read_holding(R.SisHolding.LEVEL_HMI))

    def hmi_trip_status(self) -> bool:
        return self.sis.read_coil(R.SisCoil.TRIP_STATUS)

    def close(self) -> None:
        self.bpcs.close()
        self.sis.close()
