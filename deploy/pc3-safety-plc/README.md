# PC3 — Safety PLC (SIS) — the attack target

Runs **one** OpenPLC v3 container: the Safety Instrumented System. It is a
**Modbus TCP slave**, independent of PC2's BPCS — holds the high-high trip
logic and the maintenance bypass (`SIS_OVERRIDE`) the attack defeats.

## Setup

1. Install Docker.
2. From this directory:
   ```
   cd deploy/pc3-safety-plc
   docker compose up -d
   ```
3. Confirm: `docker ps` shows `petrotwin-sis`; `curl -I http://localhost:8082/`
   answers.
4. **Open the two ports to the L2**: `502` (mapped to host `5021`) and
   `8080` (mapped to host `8082`), reachable from PC1, PC4, and the attacker:
   ```
   sudo ufw allow 5021/tcp
   sudo ufw allow 8082/tcp
   ```
5. Note this PC's IP — that's `PETROTWIN_SIS_HOST` everywhere else.

## This is the box the demo is about

The whole point of the testbed is that an attacker on the L2 can reach this
PLC's `SIS_OVERRIDE` coil over Modbus and freeze `SIS_PV_HMI` while the real
process (PC1) runs past failure. Nothing special to configure for that here
— the attack surface is just "this Modbus slave is reachable on the
network", which bringing it up on its own PC on the L2 makes literally true
instead of a loopback simulation of it.

## What NOT to do here

Same as PC2 — don't load a program by hand; PC1's `load_plant` loads both
PLCs together so the SIS trip threshold always matches
`common/scenarios.py`.

## Stop / reset

```
docker compose down
docker compose restart sis
```
If a trip is latched and you want a clean state without a full container
restart, use the SIS reset pushbutton from the HMI (PC4) or
`attack/attack.py --reset` — it only clears once the PV is a deadband below
the trip (`plc/sis/sis.st`).
