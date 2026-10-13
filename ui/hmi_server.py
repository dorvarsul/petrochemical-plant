"""PetroTwin standalone HMI server (Phase 2.5, PC4) — the spoofable operator
view with NO physics access.

``ui/plant_server.py`` bundles the 3D ground-truth view and an HMI tab in one
process fed by one ``PlantBridge`` that holds BOTH the true physics state and
the live PLC reads — fine on one host (Phase 0–2), but not what "spoofable
HMI vs. unspoofable 3D on two different hosts" is supposed to mean once the
four layers split across four PCs (`ROADMAP.md` Phase 2.5; `ARCHITECTURE.md`
"Deployment topology"). This module is the real, standalone PC4 HMI: it is a
**Modbus TCP master that polls the two real OpenPLC slaves directly**
(``ModbusUnitView`` over ``common.registers``, the exact same per-unit offset
addressing ``PlantBridge``'s live path uses) and it never imports
``physics.plant`` — there is no ground truth in this process to leak, by
construction, so "spoofable" is structural here, not just a labelling choice.

The one exception is P-401 (product storage): it has no BPCS/SIS pair (see
``common/scenarios.py`` ``ProductStorage`` / ``physics/product.py``) and its
level ODE depends on nothing but the EXPORT valve state — which IS a real
coil (``BPCS_OPV_OUT``, the vessel's BPCS). So this process keeps its own tiny
``ProductTankModel`` replica, driven only by that Modbus-read coil. No
register-map or scenario change.

Serves exactly one page, ``ui/static/hmi.html`` — shared verbatim with
``plant_server``'s bundled ``/hmi`` tab (its JS only ever reads ``hmi``/meta
fields, never ``truth``) — at ``/`` and ``/hmi``, off the same ``/api/state``
/ ``/api/attack`` / ``/api/standdown`` / ``/api/sisreset`` / ``/api/valve`` /
``/api/reset`` surface, so no frontend change was needed either.

Run (PC4, pointed at PC2/PC3 across the routed (L3) switch — see
deploy/pc4-hmi/README.md):
    PETROTWIN_BPCS_HOST=10.0.2.12 PETROTWIN_SIS_HOST=10.0.3.13 \\
        .venv/bin/python -m ui.hmi_server
then open http://127.0.0.1:8093/  (PETROTWIN_HMI_BIND_HOST=0.0.0.0 to serve
the LAN instead of just this host).

Needs pymodbus and both real OpenPLC slaves up and running their programs
(``plc.load_scenario.load_plant``, normally done by PC1's ``run_plant.sh`` /
``ui.plant_server --live``) — there is deliberately no fake-PLC fallback: this
process's whole point is that it only ever sees what the PLCs report.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bridge.plc_io import ModbusPlc, ModbusUnitView
from common import deploy_config as D
from common import registers as R
from common import scenarios
from physics.plant import FLOW
from physics.product import ProductTankModel

STATIC = Path(__file__).parent / "static"
DT, SETTLE = 1.0, 0.12   # sim-seconds per poll / real seconds between polls
# (matches plant_server's cadence; this process has no physics to pace, so
# there's no reason to poll any slower — valve/attack clicks feel sluggish
# otherwise. DT only paces the local P-401 replica's clock, see below.)


class HmiRunner(threading.Thread):
    """Polls both real OpenPLC slaves forever; applies attack/reset flags
    in-thread. Holds NO physics model — see module docstring."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._lock = threading.Lock()
        self._pending: list[tuple[str, str, bool]] = []
        self._reset = False
        self._stop = False
        self._link = {"bpcs": False, "sis": False}
        self._target = None
        self._frozen: dict[str, float] = {}
        self._time = 0.0
        self.product = ProductTankModel()
        self._connect()  # raises ConnectionError if a slave is unreachable
        self._snap = self._build_snapshot()

    # --- Modbus connection (direct to the two real slaves; no fallback) ----
    def _connect(self) -> None:
        self._bpcs = ModbusPlc(*D.BPCS_MODBUS)
        self._sis = ModbusPlc(*D.SIS_MODBUS)
        self.units = {uid: (ModbusUnitView(self._bpcs, R.UNIT_INDEX[uid]),
                             ModbusUnitView(self._sis, R.UNIT_INDEX[uid]))
                      for uid in FLOW}
        self._link = {"bpcs": True, "sis": True}

    def _reconnect(self) -> None:
        for c in (getattr(self, "_bpcs", None), getattr(self, "_sis", None)):
            try:
                c and c.close()
            except Exception:
                pass
        self._connect()

    # --- control surface (called from HTTP handlers) -----------------------
    def request_attack(self, unit, bypass=True):
        if unit not in FLOW:
            return False
        with self._lock:
            self._pending.append(("attack", unit, bool(bypass)))
        return True

    def request_standdown(self, unit):
        if unit not in FLOW:
            return False
        with self._lock:
            self._pending.append(("standdown", unit, False))
        return True

    def request_valve(self, valve, is_open):
        if valve not in ("fill", "export"):
            return False
        with self._lock:
            self._pending.append(("valve", valve, bool(is_open)))
        return True

    def request_sisreset(self, unit):
        if unit not in FLOW:
            return False
        with self._lock:
            self._pending.append(("sisreset", unit, False))
        return True

    def request_reset(self):
        with self._lock:
            self._reset = True

    def snapshot(self):
        with self._lock:
            return dict(self._snap)

    # --- per-cycle Modbus writes/reads ---------------------------------------
    def _apply_pending(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, []
        for verb, arg, flag in pending:
            if verb == "attack":
                sc = scenarios.get(arg)
                bpcs, sis = self.units[arg]
                self._frozen[arg] = round(
                    sc.reg_to_pv(sis.read_holding(R.SisHolding.PV_HMI)), 3)
                bpcs.write_coil(R.BpcsCoil.MAN_MODE, True)
                bpcs.write_holding(R.BpcsHolding.MAN_OUT, sc.attack_manual_out_reg)
                if flag:
                    sis.write_coil(R.SisCoil.OVERRIDE, True)
                self._target = arg
            elif verb == "standdown":
                sc = scenarios.get(arg)
                bpcs, sis = self.units[arg]
                bpcs.write_coil(R.BpcsCoil.MAN_MODE, False)
                bpcs.write_holding(R.BpcsHolding.SETPOINT, sc.setpoint_reg)
                sis.write_coil(R.SisCoil.OVERRIDE, False)
                self._frozen.pop(arg, None)
                if self._target == arg:
                    self._target = None
            elif verb == "valve":
                if arg == "fill":
                    self.units["tank"][1].write_coil(R.SisCoil.BLK_OPEN_CMD, flag)
                else:
                    self.units["vessel"][0].write_coil(R.BpcsCoil.OPV_CMD, flag)
            elif verb == "sisreset":
                _, sis = self.units[arg]
                sis.write_coil(R.SisCoil.RESET, True)
                sis.write_coil(R.SisCoil.RESET, False)

    def _poll_unit(self, uid: str) -> tuple[dict, bool]:
        bpcs, sis = self.units[uid]
        sc = scenarios.get(uid)
        hmi_pv = sc.reg_to_pv(sis.read_holding(R.SisHolding.PV_HMI))
        trip_cmd = sis.read_coil(R.SisCoil.TRIP_CMD)
        trip_status = sis.read_coil(R.SisCoil.TRIP_STATUS)
        setpoint_now = sc.reg_to_pv(bpcs.read_holding(R.BpcsHolding.SETPOINT))
        sp_tampered = abs(setpoint_now - sc.setpoint) > 0.5 / sc.reg_scale
        man_mode = bpcs.read_coil(R.BpcsCoil.MAN_MODE)
        override = sis.read_coil(R.SisCoil.OVERRIDE)
        attacked = bool(override or man_mode or sp_tampered)
        feed = R.reg_to_valve(bpcs.read_holding(R.BpcsHolding.FEED_CMD))
        meta = scenarios.hmi_meta(sc)
        meta.update({
            "hmi": {"pv": round(hmi_pv, 3),
                    "fraction": round(sc.fraction(hmi_pv), 4),
                    "trip_status": bool(trip_cmd or trip_status)},
            "feed": round(feed, 3), "trip": bool(trip_cmd),
            "setpoint_now": round(setpoint_now, 3),
            "setpoint_frac": round(sc.fraction(setpoint_now), 4),
            "setpoint_tampered": sp_tampered, "override": bool(override),
            "attacked": attacked, "frozen_hmi": self._frozen.get(uid),
            "mode": "MAN" if man_mode else "AUTO",
            "sp_clamped": bool(bpcs.read_coil(R.BpcsCoil.SP_CLAMPED)),
            "pv_hi": bool(bpcs.read_coil(R.BpcsCoil.PV_HI_ALARM)),
            "pv_lo": bool(bpcs.read_coil(R.BpcsCoil.PV_LO_ALARM)),
            "prealarm_h": bool(sis.read_coil(R.SisCoil.PREALARM_H)),
            "bypass_active": bool(sis.read_coil(R.SisCoil.BYPASS_ACTIVE)),
            "pv_bad": bool(sis.read_coil(R.SisCoil.PV_BAD)),
        })
        if uid == "tank":
            meta["fill_open"] = bool(sis.read_coil(R.SisCoil.BLK_OUT))
        return meta, attacked

    def _build_snapshot(self) -> dict:
        units, any_attacked = {}, False
        for uid in FLOW:
            meta, attacked = self._poll_unit(uid)
            units[uid] = meta
            any_attacked = any_attacked or attacked
        export_open = bool(self.units["vessel"][0].read_coil(R.BpcsCoil.OPV_OUT))
        self.product.step(DT, export_open=export_open)
        self._time += DT
        return {
            "status": "running", "time": round(self._time, 1), "flow": list(FLOW),
            "target": self._target, "any_attacked": any_attacked,
            "units": units, "product": self.product.snapshot(),
            "live": {"enabled": True, **self._link},
        }

    def run(self) -> None:
        while not self._stop:
            if self._reset:
                with self._lock:
                    self._reset = False
                    self._pending = []
                self._target = None
                self._frozen = {}
                self.product.reset()
                self._time = 0.0
                try:
                    from plc.load_program import restart_runtime
                    restart_runtime(D.BPCS_WEB)
                    restart_runtime(D.SIS_WEB)
                except Exception as e:
                    print(f"[hmi_server] reset error: {e}")
                try:
                    self._reconnect()
                    with self._lock:
                        self._snap = self._build_snapshot()
                except Exception as e:
                    print(f"[hmi_server] reset reconnect error: {e}")
                continue
            self._apply_pending()
            try:
                snap = self._build_snapshot()
                self._link = {"bpcs": True, "sis": True}
            except Exception as e:
                self._link = {"bpcs": False, "sis": False}
                print(f"[hmi_server] poll error: {e}; reconnecting")
                try:
                    self._reconnect()
                except Exception:
                    pass
                time.sleep(0.5)
                continue
            with self._lock:
                self._snap = snap
            time.sleep(SETTLE)


RUNNER: HmiRunner | None = None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            return {}

    def do_GET(self):
        if self.path in ("/", "/hmi", "/hmi.html", "/index.html"):
            try:
                self._send(200, (STATIC / "hmi.html").read_bytes(),
                           "text/html; charset=utf-8")
            except OSError:
                self._send(500, "hmi.html missing", "text/plain")
        elif self.path.startswith("/api/state"):
            self._send(200, json.dumps(RUNNER.snapshot()), "application/json")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path.startswith("/api/attack"):
            body = self._json_body()
            bypass = bool(body.get("bypass", not body.get("setpoint_only", False)))
            ok = RUNNER.request_attack(body.get("unit", ""), bypass)
            self._send(200 if ok else 400, json.dumps({"ok": ok}), "application/json")
        elif self.path.startswith("/api/standdown"):
            ok = RUNNER.request_standdown(self._json_body().get("unit", ""))
            self._send(200 if ok else 400, json.dumps({"ok": ok}), "application/json")
        elif self.path.startswith("/api/valve"):
            body = self._json_body()
            ok = RUNNER.request_valve(body.get("valve", ""), bool(body.get("open", False)))
            self._send(200 if ok else 400, json.dumps({"ok": ok}), "application/json")
        elif self.path.startswith("/api/sisreset"):
            ok = RUNNER.request_sisreset(self._json_body().get("unit", ""))
            self._send(200 if ok else 400, json.dumps({"ok": ok}), "application/json")
        elif self.path.startswith("/api/reset"):
            RUNNER.request_reset()
            self._send(200, json.dumps({"ok": True}), "application/json")
        else:
            self._send(404, "not found", "text/plain")

    def log_message(self, *args):
        pass  # quiet


def main() -> None:
    global RUNNER
    try:
        RUNNER = HmiRunner()
    except (ConnectionError, OSError) as e:
        raise SystemExit(
            f"[hmi_server] could not reach the PLCs: {e}\n"
            f"  BPCS expected at {D.BPCS_MODBUS[0]}:{D.BPCS_MODBUS[1]} "
            f"(PETROTWIN_BPCS_HOST/_MODBUS_PORT)\n"
            f"  SIS  expected at {D.SIS_MODBUS[0]}:{D.SIS_MODBUS[1]} "
            f"(PETROTWIN_SIS_HOST/_MODBUS_PORT)\n"
            "See deploy/pc4-hmi/README.md. This process has no fake-PLC "
            "fallback — it must see the real PLCs.")
    RUNNER.start()
    httpd = ThreadingHTTPServer((D.HMI_BIND_HOST, D.HMI_PORT), Handler)
    print("PetroTwin standalone HMI (PC4, Modbus-only, no physics access) on "
          f"http://{D.HMI_BIND_HOST}:{D.HMI_PORT}/\n"
          f"  polling BPCS {D.BPCS_MODBUS[0]}:{D.BPCS_MODBUS[1]}, "
          f"SIS {D.SIS_MODBUS[0]}:{D.SIS_MODBUS[1]}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
