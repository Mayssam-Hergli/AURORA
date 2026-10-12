#!/usr/bin/env bash
# Pre-demo checklist (MASTER_PLAN Phase 9). Run in WSL from the repo root: bash scripts/preflight.sh
# Prints one green/red line per check; exit code = number of red lines.
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-.venv/bin/python}
red=0
ok()  { printf '\033[32m  OK  \033[0m %s\n' "$1"; }
bad() { printf '\033[31m FAIL \033[0m %s\n' "$1"; red=$((red + 1)); }
warn(){ printf '\033[33m WARN \033[0m %s\n' "$1"; }

command -v docker >/dev/null && docker ps >/dev/null 2>&1 && ok "docker running" || bad "docker not running (start Docker in WSL)"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx aurora_mqtt; then ok "broker container aurora_mqtt up"; else bad "broker down: make up"; fi
(echo > /dev/tcp/127.0.0.1/1883) 2>/dev/null && ok "MQTT port 1883 open" || bad "port 1883 closed: make up"
(echo > /dev/tcp/127.0.0.1/9001) 2>/dev/null && ok "WebSocket port 9001 open (dashboard)" || bad "port 9001 closed: make up"
docker ps --format '{{.Names}}' 2>/dev/null | grep -qx sat_b_redis && ok "Phase 2 fallback Redis (6379) up" || warn "Phase 2 fallback Redis not running (only needed for the last-resort demo)"

if [ -x "$PY" ]; then
  v=$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  [ "$v" = "3.11" ] && ok "python $v ($PY)" || warn "python $v (project uses 3.11)"
  s=$(date +%s.%N); $PY -c "import torch" 2>/dev/null; d=$(echo "$(date +%s.%N) - $s" | bc)
  awk "BEGIN{exit !($d < 15)}" && ok "torch import ${d%.*} s" || warn "torch import ${d%.*} s: slow, the core takes this long to start (venv on /mnt/c?)"
else
  bad "no venv at $PY: make setup"
fi
if ls data/models/full_s*.pt >/dev/null 2>&1; then
  n=$(ls data/models/full_s*.pt | wc -l)
  ok "$n ensemble models in data/models"
  [ -f data/models/calibration.json ] && ok "calibration.json present ($(grep -o '"temperature": [0-9.]*' data/models/calibration.json))" || bad "calibration.json missing: the core falls back to trajectory (make train)"
else
  warn "no trained models: the core uses the trajectory forecaster for every satellite"
fi
(echo > /dev/tcp/127.0.0.1/8080) 2>/dev/null && warn "port 8080 already in use (dashboard already served?)" || ok "port 8080 free for make dashboard"
free=$(df -Pm . | awk 'NR==2{print $4}'); [ "$free" -gt 2000 ] && ok "disk: ${free} MB free" || bad "disk: only ${free} MB free"
[ -x .venv-win/Scripts/python.exe ] && ok "Windows bridge venv (.venv-win) present" || warn ".venv-win missing (board bridge): see hardware/BRINGUP.md"
$PY -m pytest -q -p no:cacheprovider tests/test_schemas.py tests/test_topics.py >/dev/null 2>&1 && ok "contract tests pass" || bad "contract tests fail: python -m pytest tests/test_schemas.py"
echo
[ $red -eq 0 ] && echo "preflight: all green" || echo "preflight: $red red item(s)"
exit $red
