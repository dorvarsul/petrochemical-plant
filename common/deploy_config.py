"""Deployment host/port config — single source for every address the layers
need to find each other.

Phases 0–2 run every layer as co-operating processes on **one host**, so every
value below defaults to exactly that single-host layout (`127.0.0.1`, the same
ports `common/registers.py` and `plc/docker-compose.yml` have always used) —
importing this module changes nothing by default.

Phase 2.5 (`ROADMAP.md` / `ARCHITECTURE.md` "Deployment topology") splits the
four layers onto four physical PCs, each layer in its own VLAN/subnet behind one
layer-3 (routed) switch. Moving a layer is then **one environment variable per
address**, set on the PC that needs to reach it — no code change, no register-map
change. See `deploy/README.md`.

Everyone that needs a host/port imports it from HERE, never hard-codes it:
``common/registers.py`` (Modbus ``ENDPOINTS``), ``plc/load_scenario.py`` and
``bridge/run_live_demo.py`` (the OpenPLC web UI, used to load/compile/restart
programs), ``ui/plant_server.py`` (physics+3D bind address, PC1), and
``ui/hmi_server.py`` (the standalone PC4 HMI's bind address + the two slaves
it polls directly).
"""

from __future__ import annotations

import os


def _host(var: str, default: str = "127.0.0.1") -> str:
    return os.environ.get(var, default)


def _port(var: str, default: int) -> int:
    return int(os.environ.get(var, str(default)))


# --- Control PLC / BPCS — PC2 in the distributed layout ---------------------
BPCS_HOST = _host("PETROTWIN_BPCS_HOST")
BPCS_MODBUS_PORT = _port("PETROTWIN_BPCS_MODBUS_PORT", 5020)
BPCS_WEB_PORT = _port("PETROTWIN_BPCS_WEB_PORT", 8081)
BPCS_MODBUS = (BPCS_HOST, BPCS_MODBUS_PORT)
BPCS_WEB = f"http://{BPCS_HOST}:{BPCS_WEB_PORT}"

# --- Safety PLC / SIS — PC3 --------------------------------------------------
SIS_HOST = _host("PETROTWIN_SIS_HOST")
SIS_MODBUS_PORT = _port("PETROTWIN_SIS_MODBUS_PORT", 5021)
SIS_WEB_PORT = _port("PETROTWIN_SIS_WEB_PORT", 8082)
SIS_MODBUS = (SIS_HOST, SIS_MODBUS_PORT)
SIS_WEB = f"http://{SIS_HOST}:{SIS_WEB_PORT}"

# --- Physics + 3D — PC1 (the bridge is a Modbus master to the two slaves
# above; this is only the bind address the 3D/bundled-HMI web page listens on)
PLANT_BIND_HOST = _host("PETROTWIN_PLANT_BIND_HOST")
PLANT_PORT = _port("PETROTWIN_PLANT_PORT", 8092)

# --- Standalone HMI — PC4 (Modbus-only; no physics access in that process) --
HMI_BIND_HOST = _host("PETROTWIN_HMI_BIND_HOST")
HMI_PORT = _port("PETROTWIN_HMI_PORT", 8093)
