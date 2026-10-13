# PetroTwin — exact 4-PC distributed deployment instructions

This is the one file to follow top to bottom to stand the whole testbed up
across four physical PCs for Phase 2.5 (`ROADMAP.md` / `ARCHITECTURE.md`
"Deployment topology"). It assumes **no prior state** on any of the four
machines. For the *why* behind each piece, see `deploy/README.md` and the
per-PC notes under `deploy/`; this file is the *exact commands*, in order.

Coming back to this after the 4-PC week? See **"Returning to single-PC
development"** at the bottom — nothing here is one-way.

> **No internet on the deployment PCs?** This file assumes each PC can reach the
> internet once (to build the OpenPLC image and `pip install`). For an
> **air-gapped** site, first follow [`DEPLOY-OFFLINE.md`](DEPLOY-OFFLINE.md) to
> stage a USB kit and load it — then come back here for §2 onward, which is the
> same either way.

> **Prefer one command per PC?** Each PC has a wrapper script that runs its whole
> bring-up (image load/build + `docker run`, or venv + launch) with **no manual
> `docker compose`/`pip`**: run `deploy/pc2-control-plc/pc2.sh start`,
> `deploy/pc3-safety-plc/pc3.sh start`, then `deploy/pc1-physics-3d/pc1.sh start`,
> then `deploy/pc4-hmi/pc4.sh start`, and `deploy/attacker/attack.sh reactor
> --plant` for §9. They wrap the exact same steps spelled out below — use them or
> the manual steps, both land in the same place. Offline: `export
> KIT=/media/<usb>/petrotwin-kit` first. Still do §2 (`hosts.env`) and the §5
> reachability check by hand. See `deploy/README.md` → "One-command per-PC scripts".

## 0. What you need

- 4 Ubuntu Linux PCs, wired to one **layer-3 (routed) switch** — **one VLAN /
  subnet per layer** (PC1, PC2, PC3, PC4 each in its own), with **inter-VLAN
  routing enabled on the switch** and TCP **5020/5021** (Modbus) +
  **8081/8082** (OpenPLC web UI) permitted from the physics (PC1) and HMI (PC4)
  subnets to the PLC subnets (PC2, PC3) — the PLC containers' *host* ports, so
  the switch never sees 502. Wired, not Wi-Fi — less jitter. Do the switch
  before step 5: for the lab's **Cisco Catalyst 9200-24**, follow
  [`DEPLOY-SWITCH.md`](DEPLOY-SWITCH.md) (cabling, VLANs, SVIs, static IPs, NTP
  from the switch, and the per-VLAN ACLs).
- `git`, Python 3.10+, and (on PC2/PC3 only) Docker + the Compose plugin.
- Each PC's IP address **in its own layer's subnet** (`ip addr show` — note the
  one on the switch-facing interface, not any VPN/Wi-Fi interface). The PLCs are
  reached by routed IP, so PC1/PC4 do not need to be in the same subnet as PC2/PC3
  — only routable to them.

Role assignment (fixed by the code — don't swap these):

| PC  | Runs |
|-----|------|
| PC1 | physics + the 3D ground-truth view + the Modbus bridge |
| PC2 | the Control PLC (BPCS) container |
| PC3 | the Safety PLC (SIS) container — the attack target |
| PC4 | the standalone operator HMI |

An attack can be launched from **any** fifth machine the switch will route to the
PLC subnets, or from PC4 itself — its Modbus writes traverse the inter-VLAN
routing hop to reach the SIS (see step 8).

## 1. On every PC: get the code

```bash
git clone <this repo's URL> petrotwin   # or: git pull, if already cloned
cd petrotwin
```

PC2 and PC3 only need the two files under `deploy/pc2-control-plc/` /
`deploy/pc3-safety-plc/`, but cloning the whole repo everywhere is simplest
and lets you run `docker compose` from a normal checkout.

## 2. On every PC: the host/IP map

Do this once, then copy the **same filled-in file** to all four machines
(scp, a USB stick, whatever's convenient — it never goes in git):

```bash
cp deploy/hosts.env.example deploy/hosts.env
```

Edit `deploy/hosts.env` and set the two real IPs (PC1 and PC4 need both; PC2
only needs its own IP to not matter; PC3 likewise — just fill in all four
fields everywhere so the file is identical on every machine):

```bash
export PETROTWIN_BPCS_HOST=<PC2's IP>
export PETROTWIN_SIS_HOST=<PC3's IP>
export PETROTWIN_PLANT_BIND_HOST=0.0.0.0
export PETROTWIN_HMI_BIND_HOST=0.0.0.0
```

Leave the `_PORT` lines commented out (defaults: Modbus `5020`/`5021`, web UI
`8081`/`8082`, plant `8092`, HMI `8093`) unless you have a specific reason to
move one.

From here on, **every shell you open on any PC starts with**:

```bash
cd petrotwin
source deploy/hosts.env
```

## 3. PC2 — Control PLC (BPCS)

```bash
cd deploy/pc2-control-plc
docker compose up -d
```

First run builds the OpenPLC image from the pinned upstream commit (needs
internet once); after that it's instant. Confirm:

```bash
docker ps                          # petrotwin-bpcs, Up
curl -I http://localhost:8081/     # OpenPLC web UI answers
```

Open the firewall if `ufw` (or equivalent) is active:

```bash
sudo ufw allow 5020/tcp   # Modbus TCP
sudo ufw allow 8081/tcp   # web UI (program load/restart, from PC1)
```

## 4. PC3 — Safety PLC (SIS)

```bash
cd deploy/pc3-safety-plc
docker compose up -d
docker ps                          # petrotwin-sis, Up
curl -I http://localhost:8082/
sudo ufw allow 5021/tcp
sudo ufw allow 8082/tcp
```

## 5. Confirm PC1 can reach PC2 and PC3

Before touching PC1's Python side, from PC1's shell (with `hosts.env`
sourced):

```bash
nc -zv "$PETROTWIN_BPCS_HOST" 5020 && nc -zv "$PETROTWIN_BPCS_HOST" 8081
nc -zv "$PETROTWIN_SIS_HOST" 5021 && nc -zv "$PETROTWIN_SIS_HOST" 8082
```

All four must say `succeeded`. If any fails, it's a firewall rule on PC2/PC3
(step 3/4), or the L3 switch not routing between PC1's VLAN and the PLC VLANs
(inter-VLAN routing disabled, a missing route, or an ACL blocking 5020/5021/8081/8082)
— fix it here before continuing; nothing past this point will work until these
four connect. (`ip route get "$PETROTWIN_BPCS_HOST"` on PC1 should show the route
out via the switch.)

## 6. PC1 — Physics + 3D

One-time Python setup:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Start it:

```bash
source deploy/hosts.env
export PETROTWIN_LIVE=1
PYTHONPATH="$(pwd)" .venv/bin/python -m ui.plant_server --live
```

(`./run_plant.sh start` also works here — it probes `PETROTWIN_BPCS_HOST`/
`PETROTWIN_SIS_HOST` over the network before trying any local Docker — but
running the module directly, as above, makes the live-loading log visible
on first setup.) Watch the console for:

```
Loading CONNECTED-PLANT programs (tank+reactor+vessel) into both PLCs: ...
Loaded connected plant. Both PLCs compiled and started.
PetroTwin whole-plant view (LIVE OpenPLC) on http://0.0.0.0:8092/
```

Confirm from PC1 itself:

```bash
curl -s http://127.0.0.1:8092/api/state | python3 -m json.tool | grep -A3 '"live"'
```

Expect `"enabled": true, "bpcs": true, "sis": true`. Open
`http://<PC1-IP>:8092/` from any PC the switch routes to PC1 — this is the **3D
ground truth** (unspoofable; see `ARCHITECTURE.md`).

## 7. PC4 — standalone HMI

One-time Python setup (same as PC1, no Docker needed here):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**PC1 and PC2/PC3 must already be up** (steps 3–6) — this process has no
fake-PLC fallback; it refuses to start if it can't reach both slaves. Start
it:

```bash
source deploy/hosts.env
./run_hmi.sh start
```

Confirm:

```bash
curl -s http://127.0.0.1:8093/api/state | python3 -m json.tool | grep -A3 '"live"'
```

Same `true`/`true`/`true` expected. Open `http://<PC4-IP>:8093/` — this is
the **real distributed operator HMI**: a Modbus master with no physics
access in the process at all.

## 8. Baseline check (do this before any attack)

Watch both `http://<PC1-IP>:8092/` and `http://<PC4-IP>:8093/` for ~30
seconds: every unit should hold its setpoint, no SIS trips, both views agree
with each other. This is the connected-steady-state acceptance check
`ROADMAP.md` calls for, now re-run over the real network — don't skip it;
if the two views already disagree at baseline, something's miswired, not
attacked.

## 9. Run the attack

From a fifth machine the switch routes to the PLC subnets, or from PC4 (the
attack's Modbus writes traverse the inter-VLAN routing hop to reach the SIS):

```bash
cd petrotwin
source deploy/hosts.env
.venv/bin/python -m attack.attack reactor --plant
```

`--plant` is required here — it targets the connected whole-plant stack
PC1 is running (see `ATTACKS.md` "Live multi-unit addressing"); without it
the write silently lands on unit 0 (tank) instead of the reactor.

Expected within 1–2 minutes (`TESTING.md` has exact timings): PC4's reactor
tile freezes green (`SIS BYPASS` chip lit) while PC1's 3D view shows the
temperature climbing past the trip toward `RUNAWAY`.

Other attack variants, all needing `--plant` the same way:

```bash
.venv/bin/python -m attack.attack reactor --plant --no-bypass      # SIS left armed (defense-in-depth contrast)
.venv/bin/python -m attack.attack reactor --plant --setpoint-only  # naive attack, neutralised by the clamp
.venv/bin/python -m attack.attack reactor --plant --reset          # stand down
```

Swap `reactor` for `tank` or `vessel` to target a different unit.

## 10. Teardown (end of the week)

```bash
./run_hmi.sh stop                                   # PC4
# Ctrl-C the ui.plant_server process                # PC1
(cd deploy/pc3-safety-plc && docker compose down)    # PC3
(cd deploy/pc2-control-plc && docker compose down)   # PC2
```

## Returning to single-PC development

Nothing above is one-way. Back on your usual dev machine:

- Just **don't** `source deploy/hosts.env` (or `unset` the
  `PETROTWIN_*_HOST`/`*_BIND_HOST` vars if your shell still has them loaded).
  Every address in `common/deploy_config.py` falls back to `127.0.0.1` and
  the original ports the moment those env vars are unset — no code change,
  no revert needed.
- `./run_plant.sh start` and the existing single-host OpenPLC containers
  (`plc/docker-compose.yml`) work exactly as before.
- `ui/hmi_server.py` / `run_hmi.sh` are additive — ignore them for ordinary
  single-host work, or run `run_hmi.sh` alongside `run_plant.sh` locally any
  time you want the physics-blind HMI contrast without a second PC.
- The real `deploy/hosts.env` file (with your lab's actual IPs) stays local
  and un-committed (`.gitignore`); `deploy/hosts.env.example` is the only
  copy that lives in git, so update *that* if a future change adds a new
  address to `common/deploy_config.py`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `nc -zv` fails in step 5 | Firewall on PC2/PC3 (step 3/4), or the L3 switch not routing PC1's VLAN to the PLC VLANs (inter-VLAN routing off, missing route, or an ACL blocking 5020/5021/8081/8082) — check `ip route get <PLC-IP>` on PC1, and `show access-lists` on the switch ([`DEPLOY-SWITCH.md`](DEPLOY-SWITCH.md) §8.4) |
| PC1 logs `LIVE unavailable (...)` and falls back to fake PLCs | PC2/PC3 not up yet, wrong IP in `hosts.env`, or a port blocked — re-run step 5 |
| `hmi_server` refuses to start, names a host:port | Same as above — it has no fallback by design; fix reachability first |
| HMI and 3D disagree even with no attack running | A stale attack/bypass left over from a previous session — `curl -X POST http://<PC1-IP>:8092/api/reset` to restart both PLC runtimes cleanly |
| `attack.attack <unit>` seems to do nothing | Missing `--plant` against the connected whole-plant stack — see step 9 |
| Clock-looking weirdness, inconsistent timing between the two views | Cross-machine time sync — enable NTP on all four PCs (`timedatectl`) before re-testing; see `ROADMAP.md` "Biggest risk throughout" |
