"""PetroTwin physics layer — the simulated plant, holding the TRUE process state.

Pure-Python ODE models (no numpy/scipy), one model per scenario:

    tank    -> TankModel     (1 ODE: liquid level, overfill)
    vessel  -> VesselModel   (1 ODE: pressure, overpressure)
    reactor -> ReactorModel  (2 ODEs: temperature + concentration, runaway)

All three satisfy the ``ProcessModel`` interface in ``physics.base``, so the
Modbus bridge, HMI, and 3D view treat them uniformly. Use ``make_model(id)`` to
build the model for a scenario id from ``common.scenarios``. The physics is
ground truth: sensors are read from it and actuator commands are applied to it
via the Modbus bridge, and the 3D view reads it directly. See ARCHITECTURE.md.
"""

from __future__ import annotations

from common import scenarios
from physics.base import ProcessModel
from physics.reactor import ReactorModel
from physics.tank import TankModel
from physics.vessel import VesselModel

__all__ = [
    "ProcessModel", "TankModel", "VesselModel", "ReactorModel", "make_model",
]


def make_model(scenario_id: str) -> ProcessModel:
    """Build the physics model for a scenario id (default-configured from it)."""
    sc = scenarios.get(scenario_id)
    if sc.id == "tank":
        # The tank keeps its own rich parameterization (the proven anchor);
        # pull the demo flows from the scenario so it stays the single source.
        p = sc.physics
        return TankModel(
            area=p["area"], q_in_max=p["q_in_max"], q_out=p["q_out"],
            height_full=sc.fail_at, level_trip_hh=sc.trip_hh,
            level_setpoint=sc.setpoint, level0=sc.pv0, scenario=sc,
        )
    if sc.id == "vessel":
        return VesselModel(sc)
    if sc.id == "reactor":
        return ReactorModel(sc)
    raise KeyError(f"no physics model for scenario {sc.id!r}")
