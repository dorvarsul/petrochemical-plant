# PC3 — Safety PLC (SIS) — the attack target

> **Quick start:** `deploy/pc3-safety-plc/pc3.sh start` loads/builds the image,
> runs the container (plain `docker run`, no compose), and opens the firewall.
> Also `stop` / `restart` / `recreate` / `status` / `logs`. Offline: `export
> KIT=/media/<usb>/petrotwin-kit` first to load the image from the USB kit.

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
4. **Open the two ports across the routed network**: `502` (mapped to host
   `5021`) and `8080` (mapped to host `8082`), reachable — over the L3 switch's
   inter-VLAN routing — from PC1, PC4, and the attacker:
   ```
   sudo ufw allow 5021/tcp
   sudo ufw allow 8082/tcp
   ```
5. Note this PC's IP — that's `PETROTWIN_SIS_HOST` everywhere else.

## This is the box the demo is about

The whole point of the testbed is that an attacker whose traffic the L3 switch
routes to this VLAN can reach this PLC's `SIS_OVERRIDE` coil over Modbus and
freeze `SIS_PV_HMI` while the real process (PC1) runs past failure. Putting the
SIS on its **own VLAN/subnet** is exactly the "SIS on a separate network"
posture — the attack now has to cross the inter-VLAN routing hop (and whatever
ACLs the switch enforces) to get here, instead of sitting on a flat segment with
it. Nothing special to configure in the container for that; bringing it up on
its own PC/VLAN makes "this Modbus slave is reachable across the routed network"
literally true instead of a loopback simulation of it.

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
