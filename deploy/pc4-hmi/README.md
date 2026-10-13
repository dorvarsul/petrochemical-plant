# PC4 — standalone operator HMI

Runs `ui/hmi_server.py`: a **Modbus TCP master** that polls PC2 (BPCS) and
PC3 (SIS) directly over the network and renders the operator mimic
(`ui/static/hmi.html`). This process **never imports the physics model** —
there is no ground truth to leak here, by construction. That is the point of
putting the HMI on its own PC: "spoofable HMI" stops being a label and
becomes a structural fact about what this process can even see.

## Setup

1. Same Python venv as the other PCs: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
   (only `pymodbus`/`pyserial` are needed here — no Docker on this PC).
2. Copy `deploy/hosts.env.example` → `deploy/hosts.env`, fill in PC2/PC3's
   real IPs, and `source deploy/hosts.env`. Make sure
   `PETROTWIN_HMI_BIND_HOST=0.0.0.0` is set if you want to view the HMI from
   another PC the switch routes to PC4 (e.g. a control-room laptop); the default
   `127.0.0.1` serves PC4 only.
3. **PC2 and PC3 must already be up and have the plant programs loaded**
   (done from PC1 — see `deploy/pc1-physics-3d/README.md`). This process has
   no fake-PLC fallback; if it can't reach either slave it refuses to start
   with a message naming the host:port it tried.
4. Start:
   ```
   ./run_hmi.sh start
   ```
   or directly: `PYTHONPATH="$(pwd)" .venv/bin/python -m ui.hmi_server`.
5. Open `http://127.0.0.1:8093/` (or `http://<PC4-IP>:8093/` from another
   PC the switch routes to PC4).

## What you'll see

Identical operator mimic to `ui/plant_server.py`'s bundled `/hmi` tab (same
HTML, unmodified) — all three units, the FILL/EXPORT valves, the flare, and
P-401 — but every value on this page came off a Modbus register this
process read a moment ago, nothing more. Compare it against PC1's `/` (3D
ground truth) during an attack: this page freezes green while PC1 shows the
real excursion.

### The P-401 tile, honestly

P-401 has no BPCS/SIS pair (`ARCHITECTURE.md`) — there is no Modbus register
for its level. This process keeps a **local replica** of P-401's one-ODE
level model (`physics/product.py`), driven only by the EXPORT valve coil it
reads from PC2 over Modbus (`BPCS_OPV_OUT`) — that ODE depends on nothing
else, so the replica is exact, not an approximation, and no register-map
change was needed to show it.

## Verify

```
curl http://127.0.0.1:8093/api/state | python3 -m json.tool | head -20
```
`"live": {"enabled": true, "bpcs": true, "sis": true}` confirms it's talking
to the real PC2/PC3, not a fallback.
