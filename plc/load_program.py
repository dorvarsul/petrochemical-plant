"""Headless loader for an OpenPLC v3 program (login -> upload -> compile -> start).

Drives OpenPLC v3's web endpoints so an ST program can be loaded into a running
instance without clicking the web UI. Standard library only.

Usage:
    python3 plc/load_program.py <base_url> <st_file> <program_name>
    python3 plc/load_program.py http://127.0.0.1:8081 plc/bpcs/bpcs.st "BPCS fill control"
"""

from __future__ import annotations

import http.cookiejar
import re
import sys
import time
import urllib.parse
import urllib.request

USERNAME = "openplc"
PASSWORD = "openplc"
COMPILE_TIMEOUT = 180.0


def _opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def _post(op, url, fields=None, files=None):
    """POST form-urlencoded (fields) or multipart (with files); return body text."""
    if files:
        boundary = "----petrotwin" + str(int(time.time() * 1000))
        parts = []
        for k, v in (fields or {}).items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                         f'name="{k}"\r\n\r\n{v}\r\n')
        for k, (fname, content) in files.items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                         f'name="{k}"; filename="{fname}"\r\n'
                         f"Content-Type: application/octet-stream\r\n\r\n")
        body = b""
        for p in parts:
            body += p.encode()
        for k, (fname, content) in files.items():
            body += content if isinstance(content, bytes) else content.encode()
            body += b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    else:
        data = urllib.parse.urlencode(fields or {}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
    with op.open(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _get(op, url):
    with op.open(urllib.request.Request(url), timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def load(base_url: str, st_path: str, prog_name: str) -> None:
    """Load an ST program from a file path."""
    with open(st_path, "r") as f:
        st_content = f.read()
    load_text(base_url, st_content, prog_name, source=st_path)


def load_text(base_url: str, st_content: str, prog_name: str,
              source: str = "<generated>") -> None:
    """Load an ST program from in-memory text (login -> upload -> compile -> start).

    Used by plc/load_scenario.py, which generates the SIS program from the
    canonical template and the scenario's trip threshold so the loaded program
    always matches common/scenarios.py (no hand-copied constants to drift).
    """
    base_url = base_url.rstrip("/")
    op = _opener()

    print(f"[{base_url}] login")
    body = _post(op, f"{base_url}/login",
                 fields={"username": USERNAME, "password": PASSWORD})
    if "Invalid username or password" in body or "name='password'" in body:
        raise SystemExit("login failed (check credentials)")

    print(f"[{base_url}] upload {source}")
    body = _post(op, f"{base_url}/upload-program",
                 files={"file": ("program.st", st_content)})
    m = re.search(r"value='([^']+\.st)'\s+id='prog_file'", body)
    if not m:
        raise SystemExit("could not find uploaded filename in response")
    prog_file = m.group(1)
    print(f"[{base_url}] stored as {prog_file}")

    _post(op, f"{base_url}/upload-program-action",
          fields={"prog_name": prog_name, "prog_descr": "PetroTwin Phase-0",
                  "prog_file": prog_file, "epoch_time": str(int(time.time()))})

    print(f"[{base_url}] compile (MatIEC) ...")
    _get(op, f"{base_url}/compile-program?file={prog_file}")

    deadline = time.time() + COMPILE_TIMEOUT
    logs = ""
    while time.time() < deadline:
        logs = _get(op, f"{base_url}/compilation-logs")
        if "Compilation finished successfully!" in logs:
            print(f"[{base_url}] compile OK")
            break
        if "Compilation finished with errors!" in logs:
            print(logs)
            raise SystemExit(f"[{base_url}] COMPILE FAILED")
        time.sleep(1.0)
    else:
        print(logs[-1500:])
        raise SystemExit(f"[{base_url}] compile timed out")

    print(f"[{base_url}] start PLC")
    _get(op, f"{base_url}/start_plc")
    print(f"[{base_url}] done")


def restart_runtime(base_url: str) -> None:
    """Stop then start the runtime, re-initialising program memory (clears the
    latched SIS trip and any forced coils). Useful before a clean live run."""
    base_url = base_url.rstrip("/")
    op = _opener()
    _post(op, f"{base_url}/login",
          fields={"username": USERNAME, "password": PASSWORD})
    _get(op, f"{base_url}/stop_plc")
    time.sleep(1.5)
    _get(op, f"{base_url}/start_plc")
    time.sleep(1.5)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--restart":
        restart_runtime(sys.argv[2])
    elif len(sys.argv) == 4:
        load(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        raise SystemExit(2)
