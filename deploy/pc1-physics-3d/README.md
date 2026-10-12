# PC1 — Physics + 3D (ground truth)

Runs the Python ODE plant, the Modbus bridge (a **Modbus TCP master** reaching
PC2 + PC3), and the web server for the 3D ground-truth view — and, for
convenience, the bundled `/hmi` tab too (not the "real" distributed HMI; see
PC4). This PC must be a real machine with a GUI/browser if you want to watch
the 3D view locally, but any browser on the L2 can also point at it (the
*data* it serves never crossed the network to get computed — see
"why this stays unspoofable" below).

## Why physics + 3D stay together

The 3D ground-truth view reads the physics model **directly, in-process**
(shared Python state) — never over Modbus, never through a PLC. That is the
whole reason it is unspoofable. Splitting physics and 3D onto different PCs
would force that read across the network and break the invariant, so it is
deliberately **not done** — see `ARCHITECTURE.md` "The two data taps".

Moving physics+3D itself onto its own PC does **not** weaken this: the
invariant is about the *computation* path (in-process, no Modbus/PLC hop),
not about where a browser happens to render the page. A browser on PC4 (or
your laptop) opening `http://<PC1-IP>:8092/` is fine.

## Setup

1. One-time, same as single-host: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
2. Copy `deploy/hosts.env.example` to `deploy/hosts.env`, fill in PC2/PC3's
   real IPs, and `source deploy/hosts.env`.
3. Make sure `PETROTWIN_PLANT_BIND_HOST=0.0.0.0` is set (in `hosts.env` or
   exported) if you want to view the 3D tab from another PC on the L2;
   leave it at the `127.0.0.1` default to view only locally on PC1.
4. Start: `./run_plant.sh start` (from the repo root). This:
   - brings up... **nothing** on PC1 itself (PC2/PC3's OpenPLC containers run
     on *their* PCs now — PC1 only needs network access to them, not Docker).
     `run_plant.sh`'s own `docker compose` bring-up step is for the
     **single-host** case; see the note below for the distributed case.
   - starts `ui.plant_server --live` (reads `PETROTWIN_LIVE` the same as
     always; `run_plant.sh` sets it once it confirms both slaves are
     reachable — see the note below), which loads the 3-unit programs into
     PC2/PC3 and opens the 3D tab (`/`) and the bundled HMI tab (`/hmi`).

### Distributed note on `run_plant.sh`

`run_plant.sh`'s `ensure_openplc()` step checks for **local** Docker
containers named `petrotwin-bpcs`/`petrotwin-sis`, which won't exist on PC1
in the distributed layout (they're on PC2/PC3 now). On PC1, either:

- run `PETROTWIN_LIVE=1 PYTHONPATH="$(pwd)" .venv/bin/python -m ui.plant_server --live`
  directly (skips the local-container check entirely), after confirming
  PC2/PC3 are up (see their READMEs) — **this is the supported distributed
  path**; or
- adapt `ensure_openplc()` to probe `PETROTWIN_BPCS_HOST:PETROTWIN_BPCS_MODBUS_PORT`
  / `..._SIS_...` over the network instead of `docker inspect`, if you want
  `./run_plant.sh start` to keep working unmodified across the split. Not
  done here — flagged as a small follow-up, not a blocker.

## Verify

```
curl http://127.0.0.1:8092/api/state | python3 -m json.tool | head -20
```
`"live": {"enabled": true, "bpcs": true, "sis": true}` confirms the bridge
reached both real PLCs over the network.
