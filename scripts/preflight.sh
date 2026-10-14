#!/usr/bin/env bash
# Pre-demo checklist (MASTER_PLAN Phase 9). Run in WSL from the repo root, ~1 minute:
#     PY=$HOME/aurora-venv/bin/python bash scripts/preflight.sh
# One line per check: OK (green), WARN (yellow, demo still possible), FAIL (red, fix first).
# Exit code = number of FAIL lines.
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-$HOME/aurora-venv/bin/python}
[ -x "$PY" ] || PY=.venv/bin/python
red=0; warns=0
ok()   { printf '\033[32m  OK  \033[0m %s\n' "$1"; }
bad()  { printf '\033[31m FAIL \033[0m %s\n' "$1"; red=$((red + 1)); }
warn() { printf '\033[33m WARN \033[0m %s\n' "$1"; warns=$((warns + 1)); }
port() { (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe

echo "== power and machine"
if [ -x "$PS" ]; then
  bat=$("$PS" -NoProfile -Command "(Get-CimInstance Win32_Battery).BatteryStatus" 2>/dev/null | tr -d '\r')
  case "$bat" in
    2|6|7|8|9) ok "laptop on mains power" ;;
    "") warn "power state unknown (no battery reported)" ;;
    *) bad "laptop on BATTERY: Windows throttles the CPU and the core falls behind; plug in" ;;
  esac
fi
free=$(df -Pm . | awk 'NR==2{print $4}'); [ "$free" -gt 2000 ] && ok "disk: ${free} MB free" || bad "disk: only ${free} MB free"

echo "== broker"
command -v docker >/dev/null && docker ps >/dev/null 2>&1 && ok "docker running" || bad "docker not running (start Docker in WSL)"
docker ps --format '{{.Names}}' 2>/dev/null | grep -qx aurora_mqtt && ok "broker container aurora_mqtt up" || bad "broker down: make up"
port 1883 && ok "MQTT 1883 open" || bad "MQTT 1883 closed: make up"
port 9001 && ok "WebSocket 9001 open (the dashboard needs it)" || bad "WebSocket 9001 closed: make up"
docker ps --format '{{.Names}}' 2>/dev/null | grep -qx sat_b_redis && ok "Phase 2 fallback Redis (6379) up" || warn "Phase 2 fallback Redis not running (only for the last-resort demo)"

echo "== software"
if [ -x "$PY" ]; then
  v=$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  [ "$v" = "3.11" ] && ok "python $v ($PY)" || warn "python $v (project uses 3.11)"
  s=$(date +%s); $PY -c "import torch" 2>/dev/null; d=$(( $(date +%s) - s ))
  [ "$d" -lt 15 ] && ok "torch imports in ${d} s" || warn "torch import ${d} s: the core starts slowly (use VENV=\$HOME/aurora-venv)"
  $PY -m pytest -q -p no:cacheprovider tests/test_schemas.py tests/test_topics.py >/dev/null 2>&1 && ok "contract tests pass" || bad "contract tests fail: $PY -m pytest tests/test_schemas.py"
else
  bad "no Python at $PY: make setup, or PY=\$HOME/aurora-venv/bin/python"
fi

echo "== models"
n=$(ls data/models/full_s*.pt 2>/dev/null | wc -l)
[ "$n" -ge 2 ] && ok "$n ensemble models in data/models" || bad "no trained models: make models (downloads models-v2)"
if [ -f data/models/calibration.json ]; then
  ok "calibration.json present ($(grep -o '"tau": [0-9.]*' data/models/calibration.json))"
else
  bad "calibration.json missing: the core would fall back to the trajectory forecaster (make models)"
fi
pin=$(grep -o 'MODELS_SHA256 = "[0-9a-f]*"' eval/datasets/download.py | cut -d'"' -f2)
if [ -f data/aurora-models-v2.zip ]; then
  [ "$(sha256sum data/aurora-models-v2.zip | cut -c1-64)" = "$pin" ] && ok "installed bundle matches the pinned SHA-256" || warn "data/aurora-models-v2.zip differs from the pin"
fi

echo "== dashboard"
for f in dashboard/index.html dashboard/app.js dashboard/store.js dashboard/vendor/mqtt.min.js dashboard/vendor/uPlot.iife.min.js dashboard/fonts/barlow-600.woff2; do
  [ -f "$f" ] || { bad "missing $f"; continue; }
done
[ -f dashboard/fonts/barlow-600.woff2 ] && ok "dashboard files and fonts present (works offline)"
if port 8080; then
  curl -s http://127.0.0.1:8080/ 2>/dev/null | grep -q "AURORA ground segment" && ok "dashboard already served on 8080" || warn "port 8080 busy with something else"
else
  ok "port 8080 free for make dashboard"
fi

echo "== board (satellite B)"
[ -x .venv-win/Scripts/python.exe ] && ok "Windows bridge venv (.venv-win) present" || warn ".venv-win missing: see hardware/BRINGUP.md"
if [ -x "$PS" ]; then
  ports=$("$PS" -NoProfile -Command "Get-CimInstance Win32_PnPEntity | Where-Object { \$_.Name -match 'CP210|CH340|USB-SERIAL|USB Serial' } | ForEach-Object { \$_.Name }" 2>/dev/null | tr -d '\r')
  if [ -n "$ports" ]; then
    ok "USB-serial board found: $ports  -> use that COMx in the bridge command"
  else
    warn "no USB-serial board found (only Bluetooth ports?): plug the NodeMCU in with a data cable, or run make demo-board-dry"
  fi
fi

echo
if [ $red -eq 0 ]; then
  printf '\033[32mpreflight: ALL GREEN\033[0m (%d warning(s))\n' "$warns"
else
  printf '\033[31mpreflight: %d RED item(s)\033[0m, %d warning(s): fix the red ones first\n' "$red" "$warns"
fi
exit $red
