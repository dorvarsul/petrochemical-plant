"""PetroTwin physics layer — the simulated plant, holding the TRUE process state.

Phase 0: pure-Python ODE models (no numpy/scipy), one model per scenario.
The physics is ground truth: sensors are read from it and actuator commands are
applied to it via the Modbus bridge, and the 3D view reads it directly. See
ARCHITECTURE.md.
"""
