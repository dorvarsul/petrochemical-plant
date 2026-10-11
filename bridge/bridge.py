"""The Modbus bridge loop (scenario-agnostic).

Couples a scenario physics model to the Control PLC (BPCS) and Safety PLC (SIS).
Each cycle:

  1. publish the true PV to both PLCs as a sensor reading (holding %QW0),
  2. let the PLCs scan,
  3. read the actuator command (BPCS) and the trip command (SIS),
  4. apply both to the physics: ``model.step(dt, feed_cmd, trip)`` — the model
     decides how the trip acts (feed shut / relief open / max cooling),
  5. that one step integrates the physics.

The bridge does not know which scenario it is running: it scales the PV through
``model.scenario`` and hands the raw (feed_cmd, trip) pair to the model. The 3D
ground-truth view reads ``bridge.model`` directly (never these Modbus values) —
that direct tap is what makes it unspoofable. The HMI values the operator would
see are exposed via ``hmi_pv`` / ``hmi_trip_status``, which read them back from
the (possibly compromised) SIS.
"""

from __future__ import annotations

from dataclasses import dataclass

from common import registers as R
from bridge.plc_io import PlcClient
from physics.base import ProcessModel


@dataclass
class BridgeSample:
    """One cycle's record — the ground truth vs. what the operator sees."""
    time: float
    true_pv: float          # true process variable (ground truth / 3D)
    feed: float             # effective BPCS drive applied to the physics
    trip_cmd: bool          # SIS trip asserted this cycle
    hmi_pv: float           # PV as shown on the operator HMI (SIS-reported)
    hmi_trip_status: bool   # operator indicator: True = tripped, False = armed

    @property
    def divergence(self) -> float:
        """|true PV − HMI PV| — the spoof gap (acceptance crit. F)."""
        return abs(self.true_pv - self.hmi_pv)


class Bridge:
    def __init__(self, model: ProcessModel, bpcs: PlcClient, sis: PlcClient,
                 dt: float = 0.1) -> None:
        self.model = model
        self.scenario = model.scenario
        self.bpcs = bpcs
        self.sis = sis
        self.dt = dt

    def step(self) -> BridgeSample:
        # 1. publish true PV to both PLCs (sensor injection into %QW0)
        pv_reg = self.scenario.pv_to_reg(self.model.pv)
        self.bpcs.write_holding(R.BpcsHolding.PV, pv_reg)
        self.sis.write_holding(R.SisHolding.PV, pv_reg)

        # 2. let the PLCs run their logic (no-op for a real OpenPLC)
        self.bpcs.scan()
        self.sis.scan()

        # 3. read actuator command and trip
        feed_cmd = R.reg_to_valve(self.bpcs.read_holding(R.BpcsHolding.FEED_CMD))
        trip = self.sis.read_coil(R.SisCoil.TRIP_CMD)

        # 4/5. the model applies the trip in its own scenario-specific way
        self.model.step(self.dt, feed_cmd=feed_cmd, trip=trip)

        return BridgeSample(
            time=self.model.time,
            true_pv=self.model.pv,
            feed=self.model.applied_feed,
            trip_cmd=trip,
            hmi_pv=self.hmi_pv(),
            hmi_trip_status=self.hmi_trip_status(),
        )

    # --- Operator-facing (HMI) reads, off the SIS registers ---
    def hmi_pv(self) -> float:
        return self.scenario.reg_to_pv(self.sis.read_holding(R.SisHolding.PV_HMI))

    def hmi_trip_status(self) -> bool:
        return self.sis.read_coil(R.SisCoil.TRIP_STATUS)

    def close(self) -> None:
        self.bpcs.close()
        self.sis.close()
