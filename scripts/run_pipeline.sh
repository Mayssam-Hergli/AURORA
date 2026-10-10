#!/usr/bin/env bash
# Sequential pipeline runner (one heavy job at a time: safe on a laptop with little free memory).
#   bash scripts/run_pipeline.sh [first_step]      steps: train evaluate closed_loop robustness export test
# Each step is resumable; logs and a summary go to results/logs/.
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-python}
mkdir -p results/logs
summary=results/logs/pipeline_summary.txt
steps=(train evaluate closed_loop robustness export test)
start=${1:-train}
go=0
for s in "${steps[@]}"; do
  [ "$s" = "$start" ] && go=1
  [ $go = 1 ] || continue
  t0=$(date +%s)
  case $s in
    train)       $PY -u -m ml.train all --parallel 1 ;;
    evaluate)    $PY -u -m eval.harness run --workers 1 ;;
    closed_loop) $PY -u -m eval.closed_loop --episodes 24 ;;
    robustness)  $PY -u -m eval.robustness --episodes 12 ;;
    export)      $PY -u -m ml.export ;;
    test)        $PY -m pytest -q ;;
  esac > "results/logs/step_$s.log" 2>&1
  code=$?
  printf '%-12s exit=%d %5ds\n' "$s" "$code" "$(( $(date +%s) - t0 ))" | tee -a "$summary"
  [ $code = 0 ] || exit $code
done
echo "PIPELINE DONE" | tee -a "$summary"
