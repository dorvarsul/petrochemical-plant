"""Common interface for the scenario physics models.

Every scenario's model is PURE PHYSICS: it integrates the true process state and
knows nothing about Modbus, PLCs, or the attack. The Modbus bridge couples it to
the two PLCs through one uniform entry point:

    model.step(dt, feed_cmd, trip)

where ``feed_cmd`` ∈ [0,1] is the BPCS actuator command (the control PLC output)
and ``trip`` is the SIS protective action (the safety PLC output). Each model
decides how those act on its own physics — the tank shuts its feed on trip, the
vessel opens a relief valve, the reactor forces max cooling — so the bridge stays
scenario-agnostic.

Shared read-outs every model exposes (so the bridge / HMI / 3D are generic):

    pv            current true process variable (process units)
    fraction      pv / full-scale, clamped 0..1   (gauge + 3D fill)
    failed        True once the failure threshold is reached (latched)
    applied_feed  effective BPCS drive applied this step, for display
    time          process time (s)

See ``common/scenarios.py`` for the per-scenario constants.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from common.scenarios import Scenario


@runtime_checkable
class ProcessModel(Protocol):
    """The contract the bridge relies on; all three models satisfy it."""

    scenario: Scenario
    time: float
    applied_feed: float

    @property
    def pv(self) -> float: ...
    @property
    def fraction(self) -> float: ...
    @property
    def failed(self) -> bool: ...

    def reset(self) -> None: ...
    def step(self, dt: float, feed_cmd: float, trip: bool = False) -> float: ...


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x
