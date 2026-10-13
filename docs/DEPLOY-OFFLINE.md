# PetroTwin — air-gapped (offline) 4-PC deployment

Follow this when the four deployment PCs have **no internet access**. It is the
offline companion to [`DEPLOY.md`](DEPLOY.md): that file is the exact network
bring-up and assumes each PC can reach the internet once (to build the OpenPLC
image and `pip install`). This file replaces that assumption with a **staging
kit** you build once on a machine that *does* have internet, carry on a USB
stick, and load on-site.

The split is deliberate:

- **Part A — stage the kit** runs **once, on an internet-connected machine**
  (your dev box is fine). Do this *before* you travel to the air-gapped site.
- **Part B — bring up on-site** runs on the four offline PCs and uses only the
  kit.
- **Part C — rehearse** is an offline dry-run you do on the staging box *before*
  you leave, so you find a missing piece while you can still download it.

Everything after the kit is loaded is identical to [`DEPLOY.md`](DEPLOY.md) — the
register map, the topology, the attack, the role of each PC are all unchanged.
Offline only changes *how the dependencies get onto each PC*, never what runs.

---

## 1. The three things that normally need the internet

| # | What | Needed by | Online path (DEPLOY.md) | Offline path (this doc) |
|---|------|-----------|-------------------------|-------------------------|
| 1 | **The code** (this repo) | all 4 PCs | `git clone <url>` | `git bundle` on USB → `git clone` from the bundle (§A1 / §B0) |
| 2 | **The OpenPLC Docker image** `petrotwin/openplc:v3` | PC2, PC3 | `docker compose up` **builds** it from a pinned GitHub commit | build once on the staging box, `docker save` → USB → `docker load` (§A2 / §B1) |
| 3 | **The Python packages** `pymodbus`, `pyserial` | PC1, PC4, attacker | `pip install -r requirements.txt` | `pip download` into a wheelhouse → USB → `pip install --no-index` (§A3 / §B2) |

Two more things are *not* downloads but still bite you offline — **Docker Engine
+ the Compose plugin** (PC2/PC3) and **`python3-venv`** (PC1/PC4) must already be
installed, and **time sync** can't use internet NTP. Both are handled in §A4–A5.

Nothing else reaches out. In particular: OpenPLC compiles the Structured-Text
programs *inside the container* (the matiec compiler is baked into the image),
and PC1 loads those programs into the two PLCs over their LAN web UIs
(`8081`/`8082`) — both are on-LAN, so there is **no internet dependency in the
bring-up itself** once the kit is in place.

---

## Part A — Stage the kit (on a machine WITH internet)

Do all of Part A on one internet-connected machine that has **Docker** and
**Python 3** (your normal PetroTwin dev box qualifies). Mount your USB stick and
pick one directory for the whole kit:

```bash
export KIT=/media/usb/petrotwin-kit     # adjust to your mount point
mkdir -p "$KIT"
```

> **CPU architecture must match.** A saved Docker image is architecture-specific.
> Build the image (§A2) on a machine of the **same arch** as PC2/PC3
> (`uname -m` → `x86_64` = amd64, `aarch64` = arm64). The Python wheels (§A3) are
> pure-Python and are **not** arch-specific, so those travel anywhere.

### A1. The code — a git bundle

From a clean, committed checkout of this repo:

```bash
cd /path/to/petrotwin
git status --short                       # should be empty — commit anything outstanding first
git bundle create "$KIT/petrotwin.bundle" --all
```

A bundle is a single file containing the full history and all branches; on-site
you `git clone` from it like any remote (§B0). (If you'd rather not use git at
all, a `tar czf "$KIT/petrotwin.tar.gz" --exclude=.venv --exclude=.git .` of the
working tree also works — but the bundle is cleaner and preserves history.)

### A2. The OpenPLC image — build once, save to a tarball

Both PC2 and PC3 run the **same** image tag (`petrotwin/openplc:v3`), so you
build and save it **once**:

```bash
cd /path/to/petrotwin/deploy/pc2-control-plc
docker compose build                     # clones the pinned OpenPLC_v3 commit and builds the image
docker images | grep petrotwin/openplc   # confirm: petrotwin/openplc  v3  ...
docker image inspect petrotwin/openplc:v3 --format 'arch={{.Architecture}}'   # must match PC2/PC3

docker save petrotwin/openplc:v3 -o "$KIT/openplc-v3.tar"
ls -lh "$KIT/openplc-v3.tar"             # expect a few hundred MB
```

The pinned source commit lives in the compose files
(`deploy/pc2-control-plc/docker-compose.yml` and `…/pc3-safety-plc/…`), so the
build is reproducible. You only need `deploy/pc2-control-plc` to build — PC3 uses
the identical image.

### A3. The Python packages — a wheelhouse

```bash
cd /path/to/petrotwin
python3 -m pip download -r requirements.txt -d "$KIT/wheelhouse"
ls "$KIT/wheelhouse"                     # pymodbus-*.whl, pyserial-*.whl (…-py3-none-any.whl)
```

Both wheels are **pure-Python** (`py3-none-any`), so they install on any OS and
on any Python 3.x — you do not have to match the target's Python version. (If you
want to be cautious anyway, run this on a box whose Python *minor* version matches
PC1/PC4.)

### A4. Docker + venv must already be installed on the target PCs

These are not part of the USB kit — confirm them on the target machines **while
they still have a network** (or install from your distro's install media):

- **PC2 and PC3:** Docker Engine + the Compose plugin.
  `docker --version` and `docker compose version` must both answer.
- **PC1 and PC4:** Python 3.10+ **with `venv`**. Test with
  `python3 -m venv /tmp/t && echo ok && rm -rf /tmp/t`. If that fails with an
  `ensurepip`/`venv` error, install your distro's `python3-venv` package first
  (on Ubuntu: `sudo apt-get install python3-venv`).

Staging Docker/venv as offline `.deb` bundles is possible but dependency-heavy
and fragile; the reliable path is to have them installed **before** the PCs go
onto the air-gapped network.

### A5. Time sync without the internet

Cross-machine time drift is the single biggest risk for this testbed (four
processes exchanging Modbus I/O), and you won't have public NTP. Pick one:

- **Best — a local NTP source.** If your switch/router or any one PC can serve
  NTP on the lab network, point the other three at it. With `chrony` on the
  clients, in `/etc/chrony/chrony.conf`:
  ```
  server <NTP-server-IP> iburst
  ```
  then `sudo systemctl restart chrony` and check `chronyc tracking`.
- **Minimum viable — set them together.** Set all four clocks to the same time
  immediately before the run and keep the session short:
  ```bash
  sudo timedatectl set-ntp false
  sudo timedatectl set-time "2026-01-01 09:00:00"   # same value on all four PCs
  ```
  Verify `timedatectl` shows the same time on every PC before starting.

### A6. Pack the USB — manifest + checksums

```bash
cd "$KIT"
sha256sum petrotwin.bundle openplc-v3.tar wheelhouse/* > MANIFEST.sha256
cat MANIFEST.sha256
```

Your kit directory should now hold exactly:

```
petrotwin-kit/
├── petrotwin.bundle        # the code (§A1)
├── openplc-v3.tar          # the OpenPLC image, one copy for both PLC PCs (§A2)
├── wheelhouse/             # pymodbus + pyserial wheels (§A3)
│   ├── pymodbus-*.whl
│   └── pyserial-*.whl
└── MANIFEST.sha256         # checksums (§A6)
```

Eject cleanly. **Before you travel, do Part C.**

---

## Part B — Bring up on-site (fully offline)

Mount the USB on each PC and set `KIT` to its path (e.g.
`export KIT=/media/usb/petrotwin-kit`). On every PC, first verify the kit is
intact:

```bash
( cd "$KIT" && sha256sum -c MANIFEST.sha256 )    # every line must say "OK"
```

### B0. Every PC — get the code from the bundle

```bash
git clone "$KIT/petrotwin.bundle" ~/petrotwin
cd ~/petrotwin
git checkout main            # or the branch you bundled; `git branch -a` to list
```

(Tarball instead of a bundle? `mkdir ~/petrotwin && tar xzf "$KIT/petrotwin.tar.gz" -C ~/petrotwin`.)

### B1. PC2 and PC3 — load the image, start the PLC

Do this on **both** PC2 and PC3 (same tar, same image tag; each PC needs its own
loaded copy). On **PC2 (Control PLC / BPCS):**

```bash
docker load -i "$KIT/openplc-v3.tar"
docker images | grep petrotwin/openplc:v3        # confirm the image is present

cd ~/petrotwin/deploy/pc2-control-plc
docker compose up -d --no-build                  # --no-build = never try to reach GitHub
docker ps                                         # petrotwin-bpcs, Up
curl -I http://localhost:8081/                    # OpenPLC web UI answers

sudo ufw allow 5020/tcp    # Modbus TCP (skip if ufw inactive)
sudo ufw allow 8081/tcp    # web UI (PC1 loads programs over this)
```

`--no-build` is the whole offline trick here: the compose file still *names* a
GitHub build context, and `--no-build` tells Compose to use the image you just
loaded and never attempt to fetch/build it. (If you omit it, Compose will still
use the loaded image because its tag already exists — `--no-build` just makes
that guarantee explicit and fails loudly if the load didn't work.)

On **PC3 (Safety PLC / SIS)** — identical, different directory and ports:

```bash
docker load -i "$KIT/openplc-v3.tar"
cd ~/petrotwin/deploy/pc3-safety-plc
docker compose up -d --no-build
docker ps                                         # petrotwin-sis, Up
curl -I http://localhost:8082/
sudo ufw allow 5021/tcp
sudo ufw allow 8082/tcp
```

### B2. PC1 and PC4 — install the wheels into a venv

Do this on **both** PC1 and PC4:

```bash
cd ~/petrotwin
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links "$KIT/wheelhouse" -r requirements.txt
.venv/bin/python -c "import pymodbus, serial; print('deps ok', pymodbus.__version__)"
```

`--no-index` forbids any reach to PyPI; `--find-links` points pip at the
wheelhouse. If `python3 -m venv` fails, you skipped §A4 — install `python3-venv`.

### B3. From here, follow DEPLOY.md §2–§9 exactly

The dependencies are now in place, so the rest of the bring-up is **the normal
network procedure** in [`DEPLOY.md`](DEPLOY.md) — nothing about it is
offline-specific. In brief, with [`DEPLOY.md`](DEPLOY.md) open beside you:

1. **§2 — host map.** `cp deploy/hosts.env.example deploy/hosts.env`, fill in
   PC2's and PC3's real IPs, copy the *same* file to all four PCs. Every shell
   from here starts with `cd ~/petrotwin && source deploy/hosts.env`.
2. **§5 — reachability.** From PC1: `nc -zv "$PETROTWIN_BPCS_HOST" 5020`,
   `… 8081`, `nc -zv "$PETROTWIN_SIS_HOST" 5021`, `… 8082` — all must say
   *succeeded* before continuing (firewall / L3-switch inter-VLAN routing).
3. **§6 — PC1 physics+3D.** `source deploy/hosts.env; export PETROTWIN_LIVE=1;
   PYTHONPATH="$(pwd)" .venv/bin/python -m ui.plant_server --live`. Watch for
   "Loaded connected plant. Both PLCs compiled and started." (this is PC1 pushing
   the ST programs to PC2/PC3 over the LAN — no internet). Open
   `http://<PC1-IP>:8092/`.
4. **§7 — PC4 HMI.** `source deploy/hosts.env; ./run_hmi.sh start`, then open
   `http://<PC4-IP>:8093/`.
5. **§8 — baseline check.** ~30 s: both views agree, no trips.
6. **§9 — attack.** `.venv/bin/python -m attack.attack reactor --plant` (the
   `--plant` flag is required against the connected stack).

The §10 teardown and "Returning to single-PC development" notes in
[`DEPLOY.md`](DEPLOY.md) apply unchanged.

---

## Part C — Rehearse offline before you travel

This is the step that saves the trip. On the **staging box**, after building the
kit (Part A), **disconnect it from the network** and run the real on-site
commands against the kit:

```bash
# simulate the air gap
sudo ip link set <iface> down     # or physically unplug / disable Wi-Fi

# 1. image loads and runs with no network
docker load -i "$KIT/openplc-v3.tar"
cd /path/to/petrotwin/deploy/pc2-control-plc && docker compose up -d --no-build
docker ps && curl -I http://localhost:8081/        # UI answers
docker compose down

# 2. wheels resolve with no index
python3 -m venv /tmp/rehearse && \
  /tmp/rehearse/bin/pip install --no-index --find-links "$KIT/wheelhouse" -r /path/to/petrotwin/requirements.txt && \
  /tmp/rehearse/bin/python -c "import pymodbus, serial; print('ok')"
rm -rf /tmp/rehearse
```

If both succeed with the network down, the kit is complete. Re-enable the network
afterwards. If either fails, fix it now (rebuild the image, re-run `pip
download`) while you still have internet.

---

## Troubleshooting (offline-specific)

| Symptom | Cause / fix |
|---|---|
| `docker compose up` tries to pull or build, then fails resolving `github.com` | The image wasn't loaded (or loaded under a different tag). `docker load -i "$KIT/openplc-v3.tar"`, confirm `docker images \| grep petrotwin/openplc:v3`, then `docker compose up -d --no-build`. |
| `docker load` → "no space left on device" | The image is a few hundred MB uncompressed. Free space in `/var/lib/docker` on PC2/PC3. |
| Container starts but behaves oddly; `docker image inspect … Architecture` ≠ PC2/PC3 `uname -m` | You built the image on a different CPU arch. Rebuild §A2 on a machine matching the PLC PCs. |
| `pip install` tries to reach PyPI / "Could not find a version" | Missing `--no-index --find-links "$KIT/wheelhouse"`, or a wheel didn't get downloaded. Re-run §A3 and re-check `wheelhouse/`. |
| `python3 -m venv .venv` → ensurepip/venv error | `python3-venv` not installed on PC1/PC4 — install it (§A4) before the air gap. |
| `sha256sum -c` reports a mismatch | USB copy was truncated/corrupted — recopy that file and re-verify. |
| Two views disagree at baseline, or timing looks jittery | Clocks out of sync — redo §A5 (local NTP, or set all four to the same time) before retesting. This is the documented top risk. |
| `nc -zv` fails in §B3 step 2 | Firewall on PC2/PC3 or the L3 switch not routing between the layer VLANs — this is a network issue, not an offline-staging one; see [`DEPLOY.md`](DEPLOY.md) troubleshooting. |

---

## Quick reference — the whole offline flow

**Staging box, online (once):**

```bash
export KIT=/media/usb/petrotwin-kit && mkdir -p "$KIT"
git bundle create "$KIT/petrotwin.bundle" --all
( cd deploy/pc2-control-plc && docker compose build )
docker save petrotwin/openplc:v3 -o "$KIT/openplc-v3.tar"
python3 -m pip download -r requirements.txt -d "$KIT/wheelhouse"
( cd "$KIT" && sha256sum petrotwin.bundle openplc-v3.tar wheelhouse/* > MANIFEST.sha256 )
# then: Part C rehearsal with the network OFF
```

**On-site, offline (per PC):**

```bash
export KIT=/media/usb/petrotwin-kit
( cd "$KIT" && sha256sum -c MANIFEST.sha256 )
git clone "$KIT/petrotwin.bundle" ~/petrotwin && cd ~/petrotwin

# PC2 / PC3:
docker load -i "$KIT/openplc-v3.tar"
( cd deploy/pc2-control-plc && docker compose up -d --no-build )   # PC3: pc3-safety-plc

# PC1 / PC4:
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links "$KIT/wheelhouse" -r requirements.txt

# all PCs: source deploy/hosts.env, then follow DEPLOY.md §2–§9
```
