# Attacker — any host the L3 switch routes to the PLC subnets

`attack/attack.py` is unchanged — it already only ever talked to the plant
over Modbus (never to physics, never through the HMI). In the distributed
layout it becomes a **faithful on-network adversary**: run it from any
machine the switch will route to PC2/PC3's subnets (a 5th box, or PC4, or even
your own laptop plugged into the switch) and it reaches PC2/PC3 exactly the way
a real attacker would — no code path is different from the single-host demo,
only now its Modbus writes **traverse the inter-VLAN routing hop** on the L3
switch (subject to that switch's ACLs) to reach the SIS, and that routed hop is
where Phase 3 will capture the pcap.

## Setup

1. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
   (just `pymodbus`; no Docker needed on this host).
2. `source deploy/hosts.env` (or export `PETROTWIN_BPCS_HOST` /
   `PETROTWIN_SIS_HOST` yourself) so `common/registers.py` resolves PC2/PC3's
   real IPs instead of `127.0.0.1`.
3. Run the same commands as the single-host demo. `--plant` targets the
   **connected whole-plant** stack (`ui.plant_server --live`/`run_plant.sh`,
   which is what PC1 runs in this layout) so the write lands in the chosen
   unit's offset block instead of unit 0 (tank) — see `ATTACKS.md` "Live
   multi-unit addressing":
   ```
   .venv/bin/python -m attack.attack reactor --plant              # full attack
   .venv/bin/python -m attack.attack reactor --plant --no-bypass  # SIS armed (defense-in-depth)
   .venv/bin/python -m attack.attack reactor --plant --setpoint-only
   .venv/bin/python -m attack.attack reactor --plant --reset       # stand down
   ```

## What to watch while it runs

- PC1's `/` (3D ground truth) — the real PV running toward failure.
- PC4's HMI (or PC1's bundled `/hmi`) — frozen green under the bypass.

Nothing here is attacker-host-specific config: the whole point of Phase 2.5
is that this is now a real network path, not a loopback stand-in.
