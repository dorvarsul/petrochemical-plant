# PetroTwin — Phase 2.5 distributed deployment

Moves the **same software** that runs on one host (Phase 0–2) onto **four
physical Ubuntu Linux PCs behind one layer-3 (routed) switch, each layer in its
own VLAN/subnet** — one PC per layer. It is a **deployment-topology change only**
(now with basic per-layer VLAN segmentation): no change to the four
layers, the two-PLC split, the two data taps, the register map, the
scenarios, or the attack. See `ROADMAP.md` ("Distributed deployment") and
`ARCHITECTURE.md` ("Deployment topology") for the why; this folder is the
*how*.

**Start here:** **[`DEPLOY.md`](../docs/DEPLOY.md)** in `docs/` has the
exact, copy-pasteable step-by-step instructions for the full 4-PC bring-up.
This folder holds the per-PC rationale and detail that file links to. The L3
switch itself (Cisco Catalyst 9200-24: cabling, VLANs, ACLs) is
**[`DEPLOY-SWITCH.md`](../docs/DEPLOY-SWITCH.md)**.

```
  PC1  physics + 3D  (ground truth; bridge = Modbus TCP master)
        │
        ├──Modbus TCP──► PC2  Control PLC / BPCS   (OpenPLC slave)
        └──Modbus TCP──► PC3  Safety PLC  / SIS     (OpenPLC slave)

  PC4  standalone HMI (Modbus TCP master; polls PC2 + PC3, routed)

  attacker = any extra host the switch routes to the PLC subnets (attack/attack.py)

              all behind ONE L3 (routed) switch — one VLAN/subnet per layer,
              inter-VLAN routing on the switch
```

## One-command per-PC scripts (easiest)

Each PC has a single wrapper that does everything for its layer — no manual
`docker compose`, `pip`, or env juggling. On that PC (repo cloned, `cd petrotwin`):

| PC  | Command | Does |
|-----|---------|------|
| PC2 | `deploy/pc2-control-plc/pc2.sh start` | load/build the OpenPLC image, run the BPCS container, open the firewall |
| PC3 | `deploy/pc3-safety-plc/pc3.sh start`  | same, for the SIS container (the attack target) |
| PC1 | `deploy/pc1-physics-3d/pc1.sh start`  | make the venv + deps, source `hosts.env`, launch the LIVE physics + 3D server |
| PC4 | `deploy/pc4-hmi/pc4.sh start`         | make the venv + deps, source `hosts.env`, launch the standalone HMI |
| atk | `deploy/attacker/attack.sh reactor --plant` | run the §9 attack |

Order: **PC2 and PC3 first, then PC1, then PC4.** Every script also takes
`stop` / `restart` / `status` / `logs` (the two PLC scripts add `recreate` for a
clean container; the Python ones add `setup` to only build the venv). They are a
thin, no-compose convenience layer over the *exact same* images, ports, and
programs documented below and in `DEPLOY.md` — the manual `docker compose` /
`pip` steps still work identically if you prefer them.

**Offline (air-gapped):** point the scripts at the USB kit with
`export KIT=/media/<usb>/petrotwin-kit` (they also auto-detect a `petrotwin-kit`
sibling of the repo, or one under `/media` / `/mnt`). `start` then loads the
image from the kit's `openplc-v3.tar` and installs the wheels with `--no-index`
— see [`DEPLOY-OFFLINE.md`](../docs/DEPLOY-OFFLINE.md). Fill in `deploy/hosts.env`
(PC2/PC3 IPs) first, same as the manual path.

## What changed in the repo to make this possible

Nothing about the simulation, the PLC logic, or the register map. Only how a
process finds another process:

- **`common/deploy_config.py`** — the one place every host:port lives.
  Every value defaults to `127.0.0.1` / the existing Phase-0–2 ports, so
  **nothing changes unless you set an environment variable.**
  `common/registers.py` (`ENDPOINTS`), `plc/load_scenario.py`
  (`BPCS_URL`/`SIS_URL`), `bridge/run_live_demo.py`, and `ui/plant_server.py`
  all read it from there now instead of hard-coding `127.0.0.1`.
- **`ui/hmi_server.py`** (new) — the *real* distributed HMI for PC4: a
  standalone Modbus TCP master that polls the two OpenPLC slaves directly
  and **never imports the physics model** — see its docstring. Run with
  `./run_hmi.sh`. (`ui/plant_server.py`'s bundled `/hmi` tab on PC1 still
  exists too, for convenience/debugging on a single box, but the one that
  matches the Phase-2.5 diagram is this one.)
- **`deploy/pc2-control-plc/docker-compose.yml`** / **`deploy/pc3-safety-plc/docker-compose.yml`**
  (new) — `plc/docker-compose.yml` split into two one-container files, one
  per PC, so PC2 never builds/runs the SIS container and vice versa. Images,
  ports, and the pinned build source are unchanged.
- **`deploy/hosts.env.example`** (new) — the host/IP map template. Copy it
  to `deploy/hosts.env` (gitignored — it holds your lab's real IPs) and
  `source` it on every PC before launching that PC's layer.

## Per-PC guidance

| PC  | Folder | What runs there |
|-----|--------|-----------------|
| PC1 | [`pc1-physics-3d/`](pc1-physics-3d/README.md) | physics + bridge (Modbus master) + the 3D web view |
| PC2 | [`pc2-control-plc/`](pc2-control-plc/README.md) | the Control PLC (BPCS) OpenPLC container |
| PC3 | [`pc3-safety-plc/`](pc3-safety-plc/README.md) | the Safety PLC (SIS) OpenPLC container — the attack target |
| PC4 | [`pc4-hmi/`](pc4-hmi/README.md) | the standalone operator HMI |
| —   | [`attacker/`](attacker/README.md) | the external attack script — any host the switch routes to the PLC subnets |

`checklist.md` in this folder is a short pre-flight checklist; `DEPLOY.md`
(in `docs/`) is the actual bring-up walkthrough.

## Status (honest)

The host/port plumbing above is implemented, unit-tested (fake-PLC path,
unchanged), and exercised against the real OpenPLC pair **on one machine**
(loopback standing in for real PC-to-PC IPs — pymodbus TCP does not
distinguish the two, but it is not the same as a real routed, VLAN-segmented
run). What has
**not** been exercised here: an actual run across four separate physical
PCs, the L3 switch's per-layer VLANs + inter-VLAN routing, real NIC/switch
latency, and the cross-machine time-sync risk `ROADMAP.md` calls out for this
phase. Run the `checklist.md` acceptance sequence on the real hardware before
calling Phase 2.5 done.
