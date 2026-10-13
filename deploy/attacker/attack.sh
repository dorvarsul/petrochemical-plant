#!/usr/bin/env bash
# PetroTwin — external Modbus attack (DEPLOY.md §9). Run from any host the L3
# switch routes to the PLC subnets, or from PC4 itself. One command:
#
#   ./attack.sh                       # default: reactor --plant
#   ./attack.sh reactor --plant
#   ./attack.sh vessel  --plant
#   ./attack.sh tank    --plant
#   ./attack.sh reactor --plant --no-bypass       # SIS left armed (defense-in-depth contrast)
#   ./attack.sh reactor --plant --setpoint-only   # naive attack, neutralised by the clamp
#   ./attack.sh reactor --plant --reset           # stand down
#
# NOTE: --plant is REQUIRED against the connected whole-plant stack PC1 runs
# (without it the Modbus write lands on unit 0 = tank, not the unit you named).
# This wrapper ensures the venv/deps, sources deploy/hosts.env (for PC2/PC3
# IPs), then calls attack.attack. With no args it defaults to "reactor --plant".
set -uo pipefail
cd "$(dirname "$0")"                       # deploy/attacker
REPO="$(cd ../.. && pwd)"
cd "$REPO"
source deploy/common.lib.sh

ensure_venv
source_hosts

if [ "$#" -eq 0 ]; then
  echo ">> no args given — defaulting to: reactor --plant"
  set -- reactor --plant
fi

echo ">> attack.attack $*  (BPCS=${PETROTWIN_BPCS_HOST:-127.0.0.1} SIS=${PETROTWIN_SIS_HOST:-127.0.0.1})"
exec env PYTHONPATH="$REPO" .venv/bin/python -m attack.attack "$@"
