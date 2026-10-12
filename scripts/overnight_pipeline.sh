#!/usr/bin/env bash
# Full ML pipeline with wall time per step (MASTER_PLAN Phase 2, step "re-run").
#   bash scripts/overnight_pipeline.sh [first_step]
# Steps: wait_downloads generate cache train evaluate closed_loop robustness stress export figures
# Writes results/logs/pipeline_times.tsv (step, start, end, seconds, exit) and results/logs/step_<name>.log.
# A failed step is logged and the pipeline continues with the next one (later steps that need its
# output will fail too, and say so in their own log).
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-.venv/bin/python}
LOG=results/logs
mkdir -p "$LOG"
TIMES=$LOG/pipeline_times.tsv
[ -f "$TIMES" ] || printf 'step\tstart\tend\tseconds\texit\n' > "$TIMES"
export OMP_NUM_THREADS=3 MKL_NUM_THREADS=3
steps=(wait_downloads generate cache train evaluate closed_loop robustness stress export figures)
start=${1:-wait_downloads}
go=0
for s in "${steps[@]}"; do
  [ "$s" = "$start" ] && go=1
  [ $go = 1 ] || continue
  t0=$(date +%s); d0=$(date -Iseconds)
  case $s in
    wait_downloads) while pgrep -f "eval.datasets.download" > /dev/null; do sleep 15; done; tail -5 "$LOG/download.log" ;;
    generate)    $PY -u -m ml.synthetic.generate --workers 8 ;;
    cache)       $PY -u -m ml.features ;;
    train)       $PY -u -m ml.train all --parallel 4 ;;
    evaluate)    $PY -u -m eval.harness run --workers 4 ;;
    closed_loop) $PY -u -m eval.closed_loop --episodes 24 ;;
    robustness)  $PY -u -m eval.robustness --episodes 12 ;;
    stress)      $PY -u -m eval.stress ;;
    export)      $PY -u -m ml.export ;;
    figures)     $PY -u -m eval.figures ;;
  esac > "$LOG/step_$s.log" 2>&1
  code=$?
  printf '%s\t%s\t%s\t%d\t%d\n' "$s" "$d0" "$(date -Iseconds)" "$(( $(date +%s) - t0 ))" "$code" >> "$TIMES"
  echo "$(date +%H:%M:%S) $s exit=$code $(( $(date +%s) - t0 ))s"
done
echo "PIPELINE DONE $(date -Iseconds)" | tee -a "$LOG/pipeline_done.txt"
