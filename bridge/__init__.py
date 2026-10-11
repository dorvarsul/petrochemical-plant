"""PetroTwin Modbus bridge — couples the physics model to the two PLCs.

Each cycle the bridge publishes the true process state into the PLCs as sensor
readings, lets them scan, reads back their actuator/trip outputs, computes the
effective actuator command, and advances the physics. It is the single clock
for the Phase-0 loop and therefore the main time-sync surface (see ROADMAP.md).
"""
