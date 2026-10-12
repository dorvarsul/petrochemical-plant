# Phase 2.5 — bring-up order + acceptance run

Do this once per lab session, in order. Each PC's own README has the detail;
this is the sequencing.

## 0. Prep (once)

- [ ] All four PCs on the same L2 switch, wired (not Wi-Fi — less jitter).
- [ ] NTP enabled on all four (`timedatectl` shows synchronized) — the
      cross-machine time-sync risk in `ROADMAP.md` is real; don't skip this.
- [ ] `cp deploy/hosts.env.example deploy/hosts.env`, fill in the four real
      IPs, copy that file to all four PCs (or share it however your lab
      shares files) so everyone agrees on the topology.
- [ ] Each PC: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
      (PC2/PC3 don't strictly need the Python venv — they only run Docker —
      but it's harmless to have it).

## 1. PC2 — Control PLC

```
source deploy/hosts.env   # or just note this PC's own IP for later
cd deploy/pc2-control-plc && docker compose up -d
sudo ufw allow 5020/tcp && sudo ufw allow 8081/tcp   # if ufw is active
```
Confirm: `curl -I http://localhost:8081/` answers.

## 2. PC3 — Safety PLC

```
cd deploy/pc3-safety-plc && docker compose up -d
sudo ufw allow 5021/tcp && sudo ufw allow 8082/tcp
```
Confirm: `curl -I http://localhost:8082/` answers.

## 3. From PC2/PC3, confirm PC1 can actually reach them

Before going to PC1, from PC2 and PC3 (or any host), sanity-check the ports
are open across the segment — e.g. from PC1:
```
source deploy/hosts.env
nc -zv "$PETROTWIN_BPCS_HOST" 5020 && nc -zv "$PETROTWIN_BPCS_HOST" 8081
nc -zv "$PETROTWIN_SIS_HOST" 5021 && nc -zv "$PETROTWIN_SIS_HOST" 8082
```
All four should connect. If not, it's a firewall rule on PC2/PC3, not a
PetroTwin config problem — fix it here before continuing.

## 4. PC1 — Physics + 3D

```
source deploy/hosts.env
export PETROTWIN_LIVE=1
PYTHONPATH="$(pwd)" .venv/bin/python -m ui.plant_server --live
```
(see `deploy/pc1-physics-3d/README.md` for why `run_plant.sh` itself needs a
small adaptation in the distributed case). Watch the console: it should log
loading the 3-instance programs into both slaves, then
`PetroTwin whole-plant view (LIVE OpenPLC) on http://0.0.0.0:8092/`.

Confirm: `curl http://127.0.0.1:8092/api/state` → `"live": {"enabled": true, "bpcs": true, "sis": true}`.

Open `http://<PC1-IP>:8092/` from any PC on the L2 — this is the 3D ground
truth.

## 5. PC4 — standalone HMI

```
source deploy/hosts.env
./run_hmi.sh start
```
Confirm: `curl http://127.0.0.1:8093/api/state` → same `live` block, `true`/`true`/`true`.

Open `http://<PC4-IP>:8093/` — this is the real distributed operator HMI.

## 6. Baseline acceptance (no attacker)

Watch both tabs for ~30s: every unit holds its setpoint, no SIS trips, the
two tabs agree (no divergence) — the connected-steady-state check
`ROADMAP.md` calls for **before** injecting any attack, now re-run over the
real network.

## 7. Attack run (from a 5th host, or PC4, on the same L2)

```
source deploy/hosts.env
.venv/bin/python -m attack.attack reactor --plant
```
(`--plant` targets the connected whole-plant stack PC1 is running — see
`ATTACKS.md` "Live multi-unit addressing"; omitting it would silently hit
unit 0 / tank instead of the reactor.)
Expected, within ~1–2 minutes (see `TESTING.md` timings): PC4's HMI tile for
the reactor freezes green (`SIS BYPASS` chip lit) while PC1's 3D view shows
the temperature climbing past the trip toward `RUNAWAY`. Reset with
`.venv/bin/python -m attack.attack reactor --reset`, or the HMI's own
"Reset SIS" / "Reset plant" buttons.

## 8. Teardown

```
./run_hmi.sh stop                       # PC4
# Ctrl-C the ui.plant_server process    # PC1
cd deploy/pc3-safety-plc && docker compose down   # PC3
cd deploy/pc2-control-plc && docker compose down  # PC2
```
