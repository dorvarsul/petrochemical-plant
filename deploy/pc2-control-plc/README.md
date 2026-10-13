# PC2 — Control PLC (BPCS)

> **Quick start:** `deploy/pc2-control-plc/pc2.sh start` loads/builds the image,
> runs the container (plain `docker run`, no compose), and opens the firewall.
> Also `stop` / `restart` / `recreate` / `status` / `logs`. Offline: `export
> KIT=/media/<usb>/petrotwin-kit` first to load the image from the USB kit.

Runs **one** OpenPLC v3 container: the Basic Process Control System. It is a
**Modbus TCP slave** — PC1's bridge writes sensor values and reads the
actuator command; PC4's HMI and the attacker also reach it directly.

## Setup

1. Install Docker (`docker` + `docker compose`).
2. From this directory:
   ```
   cd deploy/pc2-control-plc
   docker compose up -d
   ```
   First run builds the image from the pinned official `OpenPLC_v3` source
   (needs internet access once); later runs reuse `petrotwin/openplc:v3`.
3. Confirm it's up: `docker ps` should show `petrotwin-bpcs`, and
   `curl -I http://localhost:8081/` should answer (OpenPLC's web UI,
   login `openplc`/`openplc`).
4. **Open the two ports across the routed network** (firewall): `502` (Modbus,
   mapped to host `5020`) and `8080` (web UI, mapped to host `8081`) must be
   reachable — over the L3 switch's inter-VLAN routing — from PC1 (the program
   loader + the bridge), PC4 (the HMI), and the attacker host. If `ufw` is
   active:
   ```
   sudo ufw allow 5020/tcp
   sudo ufw allow 8081/tcp
   ```
5. Note this PC's IP (`ip addr`) — that's `PETROTWIN_BPCS_HOST` everywhere
   else (`deploy/hosts.env`).

## What NOT to do here

Don't load a PLC program by hand through the web UI — PC1's
`plc.load_scenario.load_plant` generates and loads the 3-unit program for
BOTH PLCs together (control gains + setpoint clamp come from
`common/scenarios.py`). Loading it from PC1 (pointed at this PC's web UI
over the network via `PETROTWIN_BPCS_HOST`) is how it gets here — you don't
need to touch this PC again after `docker compose up -d`.

## Stop / reset

```
docker compose down          # stop
docker compose restart bpcs  # or use plc/load_program.py's restart_runtime
                              # from PC1, which also re-arms a clean state
```
