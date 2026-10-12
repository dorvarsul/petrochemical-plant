"""PetroTwin whole-plant server (Phase 2) — the entire connected flowsheet.

Runs the **connected plant** (T-101 feed tank -> R-301 reactor -> V-201 vessel)
through ``PlantBridge`` and serves two views off one ``/api/state`` feed:

  * ``/``    — the 3D ground-truth train, driven straight from the physics
               (``truth`` per unit) — **unspoofable** (`ui/static/plant.html`).
  * ``/hmi`` — the operator HMI mimic of the whole process (inflow/outflow
               valves, each unit, relief/flare, P-401), every value from that
               unit's SIS registers (``hmi``) — **spoofable** (`ui/static/hmi.html`).

Open them in two browser tabs: the HMI stays green/frozen under a SIS bypass
while the 3D shows the real excursion — the "defeat the operator" contrast.

In LIVE mode (``--live`` / ``PETROTWIN_LIVE=1``) the three units run on the REAL
OpenPLC pair over Modbus — one BPCS slave carries control for all three and one
SIS slave carries safety, each unit in its own offset register block (see
``bridge.plc_io.ModbusUnitView`` and the generated programs in
``plc.load_scenario.load_plant``). Without it, or if the containers/pymodbus are
unavailable, it falls back to the dependency-free fake-PLC path (one in-memory
BPCS + SIS per unit). The snapshot's ``live`` block reports which path is active.

The operator can attack ANY ONE unit (BPCS forced to MANUAL + SIS bypass, or
manual-drive-with-SIS-armed for the defence-in-depth contrast), toggle the
terminal FILL/EXPORT valves, and reset a latched SIS trip; the whole plant keeps
running around it and the forward couplings propagate the disturbance
downstream — exactly the Phase-1 story, now watchable as one flowsheet.

All plant/Modbus access stays in the one background thread; HTTP handlers only
read the latest snapshot and set flags, so there is no concurrency on the bridge.

Run:
    .venv/bin/python -m ui.plant_server            # fake PLCs (no Docker)
    .venv/bin/python -m ui.plant_server --live     # real OpenPLC (both containers up)
then open http://127.0.0.1:8092/
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import os

from bridge.plant_bridge import make_fake_plant_bridge, make_live_plant_bridge
from common import deploy_config as D
from common import scenarios
from physics.plant import FLOW

STATIC = Path(__file__).parent / "static"
# Bind address + the OpenPLC web UIs used to load/restart programs. Default to
# the single-host layout; PETROTWIN_PLANT_BIND_HOST=0.0.0.0 (+ PETROTWIN_BPCS_HOST
# / PETROTWIN_SIS_HOST) move this to PC1 of the Phase-2.5 distributed layout —
# see common/deploy_config.py / deploy/README.md.
HOST, PORT = D.PLANT_BIND_HOST, D.PLANT_PORT
BPCS_WEB, SIS_WEB = D.BPCS_WEB, D.SIS_WEB
DT, SETTLE = 1.0, 0.12      # sim-seconds per step / real-seconds between steps
# LIVE: drive the two REAL OpenPLC slaves (one BPCS + one SIS carrying all three
# units) instead of the in-memory fake PLCs. Set via --live or PETROTWIN_LIVE=1;
# falls back to the fake path if the containers/pymodbus are unavailable.
LIVE = os.environ.get("PETROTWIN_LIVE", "") not in ("", "0", "false", "False")
EPS_COUPLING = 1e-3         # a coupling above this reads as "active" (propagating);
                            # above baseline integrator residual, below real drive


_unit_meta = scenarios.hmi_meta  # shared with ui/hmi_server.py (common/scenarios.py)


class PlantRunner(threading.Thread):
    """Steps the connected plant forever; applies attack/reset flags in-thread."""

    def __init__(self):
        super().__init__(daemon=True)
        self._lock = threading.Lock()
        self._pending = []          # queued (verb, unit, bypass) requests
        self._reset = False
        self._stop = False
        # live-connection health, published in the snapshot so the HMI can show
        # whether it is talking to real OpenPLC (both slaves up) or the fake path.
        self._live = {"enabled": False, "bpcs": False, "sis": False}
        self.bridge = self._build_bridge(load=True)
        # which unit the LAST attack request targeted (headline only); external
        # attacks still surface per-unit via each unit's `attacked` flag.
        self._target = None
        # frozen HMI value captured at the moment each unit is attacked, so the
        # dashboard can show the spoof gap against the value the operator saw.
        self._frozen = {}
        self._snap = self._build_snapshot(status="running")

    # --- bridge construction (live OpenPLC, with fake fallback) -----------
    def _build_bridge(self, load: bool):
        """Build the plant bridge. In LIVE mode: (optionally) load the two
        multi-unit programs, then connect over Modbus; on any failure fall back
        to the dependency-free fake path so the view still runs."""
        if LIVE:
            try:
                if load:
                    from plc.load_scenario import load_plant
                    load_plant(BPCS_WEB, SIS_WEB)  # self-loads both PLC programs
                bridge = make_live_plant_bridge(dt=DT)
                self._live = {"enabled": True, "bpcs": True, "sis": True}
                return bridge
            except Exception as e:  # pymodbus missing / containers down / load failed
                print(f"[plant_server] LIVE unavailable ({e}); using fake PLCs")
                self._live = {"enabled": False, "bpcs": False, "sis": False}
        else:
            self._live = {"enabled": False, "bpcs": False, "sis": False}
        return make_fake_plant_bridge(dt=DT)

    # --- control surface (called from HTTP handlers) ----------------------
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

    # --- snapshot assembly -------------------------------------------------
    def _build_snapshot(self, status, sample=None):
        units = {}
        any_attacked = False
        for uid in FLOW:
            m = self.bridge.plant.unit(uid)
            sc = m.scenario
            meta = _unit_meta(sc)
            if sample is not None:
                u = sample.units[uid]
                setpoint_now = u.setpoint
                sp_tampered = abs(setpoint_now - sc.setpoint) > 0.5 / sc.reg_scale
                # The evolved attack drives through MANUAL mode, so "attacked"
                # now also tracks man_mode (and the bypass), not just a tampered
                # setpoint — otherwise a manual-drive attack would read as green.
                attacked = bool(u.override or u.man_mode or sp_tampered)
                any_attacked = any_attacked or attacked
                meta.update({
                    "truth": {"pv": round(u.true_pv, 3),
                              "fraction": round(sc.fraction(u.true_pv), 4),
                              "failed": u.failed},
                    "hmi": {"pv": round(u.hmi_pv, 3),
                            "fraction": round(sc.fraction(u.hmi_pv), 4),
                            "trip_status": u.hmi_trip_status},
                    "feed": round(u.feed, 3),
                    "trip": u.trip_cmd,
                    "setpoint_now": round(setpoint_now, 3),
                    "setpoint_frac": round(sc.fraction(setpoint_now), 4),
                    "setpoint_tampered": sp_tampered,
                    "override": bool(u.override),
                    "attacked": attacked,
                    "divergence": round(u.divergence, 3),
                    "frozen_hmi": self._frozen.get(uid),
                    # real-SIF annunciators (for the HMI tile chips)
                    "mode": "MAN" if u.man_mode else "AUTO",
                    "sp_clamped": bool(u.sp_clamped),
                    "pv_hi": bool(u.pv_hi),
                    "pv_lo": bool(u.pv_lo),
                    "prealarm_h": bool(u.prealarm_h),
                    "bypass_active": bool(u.bypass_active),
                    "pv_bad": bool(u.pv_bad),
                })
                if uid == "tank":
                    meta["fill_open"] = bool(sample.fill_open)
            else:
                f0 = sc.fraction(sc.pv0)
                meta.update({
                    "truth": {"pv": sc.pv0, "fraction": round(f0, 4), "failed": False},
                    "hmi": {"pv": sc.pv0, "fraction": round(f0, 4), "trip_status": False},
                    "feed": 0.0, "trip": False,
                    "setpoint_now": sc.setpoint, "setpoint_frac": round(f0, 4),
                    "setpoint_tampered": False, "override": False,
                    "attacked": False, "divergence": 0.0, "frozen_hmi": None,
                    "mode": "AUTO", "sp_clamped": False, "pv_hi": False,
                    "pv_lo": False, "prealarm_h": False, "bypass_active": False,
                    "pv_bad": False,
                })
                if uid == "tank":
                    meta["fill_open"] = True
            units[uid] = meta

        q = sample.q_coupling if sample else 0.0
        p = sample.p_coupling if sample else 0.0
        if sample is not None and sample.product is not None:
            pr = sample.product
            product = {
                "tag": pr["tag"], "pv": round(pr["pv"], 3),
                "pv_units": pr["pv_units"], "fraction": round(pr["fraction"], 4),
                "export_open": pr["export_open"],
                "ll": pr["ll"], "lo": pr["lo"], "hi": pr["hi"], "hh": pr["hh"],
            }
        else:
            spec = self.bridge.plant.product.spec
            product = {
                "tag": spec.tag, "pv": round(spec.level0, 3),
                "pv_units": spec.pv_units,
                "fraction": round(spec.fraction(spec.level0), 4),
                "export_open": True,
                "ll": False, "lo": False, "hi": False, "hh": False,
            }
        return {
            "status": status,
            "time": round(sample.time, 1) if sample else 0.0,
            "flow": list(FLOW),
            "target": self._target,
            "any_attacked": any_attacked,
            "couplings": {
                "tank_reactor": {"value": round(q, 6), "active": q > EPS_COUPLING},
                "reactor_vessel": {"value": round(p, 6), "active": p > EPS_COUPLING},
            },
            "units": units,
            "product": product,
            "live": dict(self._live),
        }

    def _apply_pending(self):
        with self._lock:
            pending, self._pending = self._pending, []
        for verb, arg, flag in pending:
            if verb == "attack":
                # Capture the HMI value the operator will be frozen at, then inject.
                self._frozen[arg] = round(self.bridge.hmi_pv(arg), 3)
                self.bridge.attack(arg, bypass=flag)
                self._target = arg
            elif verb == "standdown":
                self.bridge.stand_down(arg)
                self._frozen.pop(arg, None)
                if self._target == arg:
                    self._target = None
            elif verb == "valve":
                if arg == "fill":
                    self.bridge.set_fill_valve(flag)
                else:
                    self.bridge.set_export_valve(flag)
            elif verb == "sisreset":
                self.bridge.reset_sis(arg)

    def run(self):
        while not self._stop:
            if self._reset:
                with self._lock:
                    self._reset = False
                    self._pending = []
                self._target = None
                self._frozen = {}
                if self._live["enabled"]:
                    # Restart both runtimes (clears latched trips + forced coils),
                    # then reconnect+reprime. Programs stay loaded, so load=False.
                    try:
                        from plc.load_program import restart_runtime
                        self.bridge.close()
                        restart_runtime(BPCS_WEB)
                        restart_runtime(SIS_WEB)
                    except Exception as e:
                        print(f"[plant_server] live reset error: {e}")
                    self.bridge = self._build_bridge(load=False)
                else:
                    # A fresh fake bridge clears every latched state + couplings.
                    self.bridge = make_fake_plant_bridge(dt=DT)
                with self._lock:
                    self._snap = self._build_snapshot(status="running")
                continue
            self._apply_pending()
            try:
                sample = self.bridge.step()
                if self._live["enabled"] and not (self._live["bpcs"] and self._live["sis"]):
                    self._live["bpcs"] = self._live["sis"] = True  # recovered
            except Exception as e:
                # Modbus hiccup: mark the live link down and try to reconnect
                # (rebuilds clients; the PLCs retain their state across this).
                if self._live["enabled"]:
                    self._live["bpcs"] = self._live["sis"] = False
                    print(f"[plant_server] live step error: {e}; reconnecting")
                    try:
                        self.bridge.close()
                    except Exception:
                        pass
                    self.bridge = self._build_bridge(load=False)
                time.sleep(0.5)
                continue
            snap = self._build_snapshot(status="running", sample=sample)
            with self._lock:
                self._snap = snap
            time.sleep(SETTLE)


RUNNER: PlantRunner | None = None


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
        if self.path in ("/", "/plant", "/index.html"):
            try:
                self._send(200, (STATIC / "plant.html").read_bytes(),
                           "text/html; charset=utf-8")
            except OSError:
                self._send(500, "plant.html missing", "text/plain")
        elif self.path in ("/hmi", "/hmi.html"):
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
            # Accept the new `bypass` flag; fall back to the legacy
            # `setpoint_only` checkbox (checked == leave SIS armed).
            bypass = bool(body.get("bypass", not body.get("setpoint_only", False)))
            ok = RUNNER.request_attack(body.get("unit", ""), bypass)
            self._send(200 if ok else 400, json.dumps({"ok": ok}), "application/json")
        elif self.path.startswith("/api/standdown"):
            ok = RUNNER.request_standdown(self._json_body().get("unit", ""))
            self._send(200 if ok else 400, json.dumps({"ok": ok}), "application/json")
        elif self.path.startswith("/api/valve"):
            body = self._json_body()
            ok = RUNNER.request_valve(body.get("valve", ""),
                                      bool(body.get("open", False)))
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


def main():
    global RUNNER, LIVE
    import sys
    if "--live" in sys.argv[1:]:
        LIVE = True
    RUNNER = PlantRunner()
    RUNNER.start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    mode = "LIVE OpenPLC" if LIVE else "fake PLCs"
    print(f"PetroTwin whole-plant view ({mode}) on http://{HOST}:{PORT}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
