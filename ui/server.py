"""PetroTwin demo server — runs the live bridge loop and serves the dashboard.

A background thread runs the Bridge against the real OpenPLC slaves over Modbus
(all Modbus stays in that one thread). HTTP handlers only read the latest
snapshot and set attack/reset flags, so there is no Modbus concurrency.

The snapshot separates the two data taps explicitly:
  * ``truth`` comes from the physics model (the 3D view) — unspoofable.
  * ``hmi``   comes from the SIS registers read over Modbus — spoofable.

Run (needs the venv + both OpenPLC containers up with programs loaded):
    .venv/bin/python -m ui.server
then open http://127.0.0.1:8090/
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bridge.bridge import Bridge
from bridge.plc_io import ModbusPlc
from common import registers as R
from common import scenarios
from physics import make_model
from plc.load_program import restart_runtime
from plc.load_scenario import load_scenario

SCENARIO = os.environ.get("PETROTWIN_SCENARIO", scenarios.DEFAULT_SCENARIO)


def _scenario_meta(sc):
    """Per-scenario labels/units/thresholds the dashboard renders generically."""
    return {
        "id": sc.id, "title": sc.title, "unit_tag": sc.unit_tag,
        "pv_name": sc.pv_name, "pv_units": sc.pv_units, "pv_symbol": sc.pv_symbol,
        "setpoint": sc.setpoint, "trip_hh": sc.trip_hh, "fail_at": sc.fail_at,
        "trip_frac": round(sc.fraction(sc.trip_hh), 4),
        "sp_frac": round(sc.fraction(sc.setpoint), 4),
        "failure_word": sc.failure_word, "actuator": sc.actuator,
        "trip_action": sc.trip_action, "ids": scenarios.ids(),
    }

STATIC = Path(__file__).parent / "static"
HOST, PORT = "127.0.0.1", 8090
DT, SETTLE = 1.0, 0.12
BPCS_WEB, SIS_WEB = "http://127.0.0.1:8081", "http://127.0.0.1:8082"


class SimRunner(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._lock = threading.Lock()
        self._attack = False
        self._reset = False
        self._switch_to = None
        self._stop = False
        self.scenario = scenarios.get(SCENARIO)
        self.model = make_model(SCENARIO)
        self._snap = self._fresh_snap(status="loading")
        self.bpcs = self.sis = self.bridge = None  # connected in run(), after load

    def _fresh_snap(self, status, attacked=False):
        sc = self.scenario
        f0 = sc.fraction(sc.pv0)
        return {"time": 0.0, "status": status,
                "truth": {"level": sc.pv0, "fraction": f0, "overflowing": False},
                "hmi": {"level": sc.pv0, "fraction": f0, "trip_status": False},
                "feed": 1.0, "trip": False, "attacked": attacked,
                "scenario": _scenario_meta(sc)}

    def _connect(self, attempts=12):
        for c in (self.bpcs, self.sis):
            try:
                if c:
                    c.close()
            except Exception:
                pass
        last = None
        for _ in range(attempts):
            try:
                self.bpcs = ModbusPlc.for_instance(R.Instance.BPCS)
                self.sis = ModbusPlc.for_instance(R.Instance.SIS)
                self.bridge = Bridge(self.model, self.bpcs, self.sis, dt=DT)
                return
            except Exception as e:
                last = e
                time.sleep(0.5)
        raise last

    def request_attack(self):
        self._attack = True

    def request_reset(self):
        self._reset = True

    def request_scenario(self, sid):
        if sid in scenarios.SCENARIOS:
            self._switch_to = sid
            return True
        return False

    def _set_status(self, status):
        with self._lock:
            self._snap = dict(self._snap, status=status)

    def snapshot(self):
        with self._lock:
            return dict(self._snap)

    def _warmup(self):
        done = 0
        while done < 4:
            try:
                self.bridge.step()
                done += 1
            except Exception:
                time.sleep(0.5)
                self._connect()
            time.sleep(SETTLE)
        self.model.reset()

    def _publish(self, s, attacked):
        H = self.scenario.pv_full_scale
        with self._lock:
            self._snap = {
                "time": round(s.time, 1), "status": "running",
                "truth": {"level": round(s.true_pv, 3),
                          "fraction": round(self.model.fraction, 4),
                          "overflowing": self.model.failed},
                "hmi": {"level": round(s.hmi_pv, 3),
                        "fraction": round(min(max(s.hmi_pv / H, 0.0), 1.0), 4),
                        "trip_status": s.hmi_trip_status},
                "feed": round(s.feed, 3),
                "trip": s.trip_cmd,
                "attacked": attacked,
                "scenario": _scenario_meta(self.scenario),
            }

    def _load_scenario_live(self, sid):
        """(Re)load the scenario's PLC programs, rebuild the model, warm up."""
        self.scenario = scenarios.get(sid)
        self.model = make_model(sid)
        with self._lock:
            self._snap = self._fresh_snap(status="loading")
        load_scenario(sid, BPCS_WEB, SIS_WEB)  # login/upload/compile/start both PLCs
        self._connect()
        self._warmup()

    def run(self):
        # The server loads the chosen scenario's programs itself, so the only
        # prerequisite is that the two OpenPLC containers are up.
        self._load_scenario_live(self.scenario.id)
        attacked = False
        while not self._stop:
            if self._switch_to:
                sid, self._switch_to = self._switch_to, None
                self._attack = self._reset = False
                attacked = False
                try:
                    self._load_scenario_live(sid)
                except Exception:
                    self._set_status("error")
                    time.sleep(1.0)
                continue
            if self._reset:
                self._reset = False
                self._attack = False
                attacked = False
                self._set_status("loading")
                restart_runtime(BPCS_WEB)
                restart_runtime(SIS_WEB)
                self._connect()
                self._warmup()
            try:
                if self._attack:
                    self.sis.write_coil(R.SisCoil.OVERRIDE, True)  # the attack, over Modbus
                    attacked = True
                    self._attack = False
                s = self.bridge.step()
                self._publish(s, attacked)
            except Exception:
                time.sleep(0.5)  # transient Modbus hiccup -> reconnect and continue
                try:
                    self._connect()
                except Exception:
                    pass
            time.sleep(SETTLE)


RUNNER: SimRunner | None = None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/dashboard", "/index.html"):
            try:
                self._send(200, (STATIC / "dashboard.html").read_bytes(),
                           "text/html; charset=utf-8")
            except OSError:
                self._send(500, "dashboard.html missing", "text/plain")
        elif self.path.startswith("/api/state"):
            self._send(200, json.dumps(RUNNER.snapshot()), "application/json")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path.startswith("/api/attack"):
            RUNNER.request_attack()
            self._send(200, json.dumps({"ok": True}), "application/json")
        elif self.path.startswith("/api/reset"):
            RUNNER.request_reset()
            self._send(200, json.dumps({"ok": True}), "application/json")
        elif self.path.startswith("/api/scenario"):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                sid = body.get("id", "")
            except (ValueError, TypeError):
                sid = ""
            ok = RUNNER.request_scenario(sid)
            self._send(200 if ok else 400, json.dumps({"ok": ok}),
                       "application/json")
        else:
            self._send(404, "not found", "text/plain")

    def log_message(self, *args):
        pass  # quiet


def main():
    global RUNNER
    RUNNER = SimRunner()
    RUNNER.start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"PetroTwin dashboard on http://{HOST}:{PORT}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
