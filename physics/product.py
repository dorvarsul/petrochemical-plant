"""P-401 product-storage tank — the terminal SINK of the flowsheet.

The connected plant drains its separated product (V-201 bottoms) into a storage
tank, P-401, which the operator empties through an EXPORT valve. Unlike the
three process units P-401 is not an attack target and has no BPCS/SIS pair — it
is a plain level accumulator with alarm thresholds, added so the plant has a
real downstream level to watch and a second operator-commanded valve.

One state variable, liquid level ``L`` (m), a basic volume balance:

    dL/dt = (q_fill - (q_export if export_open else 0)) / area

``q_export == q_fill`` by design (see ``common.scenarios.ProductStorage``): with
EXPORT open the net flow is zero and the level holds steady (no baseline drift);
with EXPORT shut it fills toward HI then HH. Parameters are the single source of
truth in ``common/scenarios.py`` (``PRODUCT``); this module is pure physics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from common import scenarios
from common.scenarios import ProductStorage


@dataclass
class ProductTankModel:
    """Single-ODE level model for the P-401 product-storage tank."""

    spec: ProductStorage = field(default_factory=lambda: scenarios.PRODUCT)

    # --- State (set in __post_init__ / reset) ---
    level: float = field(init=False)
    time: float = field(init=False)
    export_open: bool = field(init=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.level = self.spec.level0
        self.time = 0.0
        self.export_open = True

    # --- derived read-outs ---------------------------------------------------
    @property
    def fraction(self) -> float:
        return self.spec.fraction(self.level)

    @property
    def ll(self) -> bool:
        return self.level <= self.spec.ll

    @property
    def lo(self) -> bool:
        return self.level <= self.spec.lo

    @property
    def hi(self) -> bool:
        return self.level >= self.spec.hi

    @property
    def hh(self) -> bool:
        return self.level >= self.spec.hh

    # --- integration ---------------------------------------------------------
    def step(self, dt: float, export_open: bool = True) -> float:
        """Advance the level by ``dt`` seconds. ``export_open`` opens the draw;
        when shut the tank fills. Explicit Euler, level clamped to the shell."""
        if dt <= 0:
            raise ValueError("dt must be > 0")
        self.export_open = bool(export_open)
        draw = self.spec.q_export if self.export_open else 0.0
        dLdt = (self.spec.q_fill - draw) / self.spec.area
        raw = self.level + dLdt * dt
        self.level = 0.0 if raw < 0.0 else self.spec.height_full \
            if raw > self.spec.height_full else raw
        self.time += dt
        return self.level

    def snapshot(self) -> dict:
        s = self.spec
        return {
            "tag": s.tag,
            "pv_name": s.pv_name,
            "pv": self.level,
            "pv_units": s.pv_units,
            "fraction": self.fraction,
            "export_open": self.export_open,
            "ll": self.ll, "lo": self.lo, "hi": self.hi, "hh": self.hh,
        }
