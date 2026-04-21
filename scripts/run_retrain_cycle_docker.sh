#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ai-news-mlops/MLOps}"
IMAGE_NAME="${IMAGE_NAME:-ai-news-mlops:latest}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
LOCAL_DATA_DIR="${LOCAL_DATA_DIR:-$APP_DIR/local_data}"
REVIEW_LIMIT="${REVIEW_LIMIT:-200}"
EPOCHS="${EPOCHS:-1}"
MAX_SAMPLES="${MAX_SAMPLES:-100}"
FORCE_PROMOTE="${FORCE_PROMOTE:-false}"

cd "$APP_DIR"
mkdir -p "$LOCAL_DATA_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 2
fi

set -a
source "$ENV_FILE"
set +a

echo "[RETRAIN] start $(date -u)"

docker build -t "$IMAGE_NAME" .

run_py() {
  docker run --rm \
    --env-file "$ENV_FILE" \
    -v "$LOCAL_DATA_DIR:/app/local_data" \
    -v ai_news_hf_cache:/cache/huggingface \
    "$IMAGE_NAME" "$@"
}

if [ "${SKIP_EXPORT:-false}" != "true" ]; then
  echo "[RETRAIN] exporting label review CSV"
  run_py python label_review.py export --limit "$REVIEW_LIMIT"

  cat <<EOF

[ACTION REQUIRED]
Review this file before continuing:
  $LOCAL_DATA_DIR/review/label_review.csv

Mark rows as:
  accept  -> keep suggested entity/type
  correct -> fill corrected_entity and corrected_type
  reject  -> ignore the span

Then re-run this script with:
  SKIP_EXPORT=true bash scripts/run_retrain_cycle_docker.sh
EOF
  exit 0
fi

echo "[RETRAIN] building reviewed datasets"
run_py python label_review.py build-datasets

echo "[RETRAIN] evaluating current model"
run_py python eval.py --model-prefix models/current --upload-results --current-result

echo "[RETRAIN] training candidate model"
run_py python train.py --epochs "$EPOCHS" --max-samples "$MAX_SAMPLES" --overwrite

echo "[RETRAIN] evaluating candidate model"
run_py python eval.py --model-prefix models/candidate --upload-results --candidate-result

echo "[RETRAIN] checking promotion gate"
if run_py python eval.py --check-gate; then
  echo "[RETRAIN] promotion gate passed"
  run_py python promote_model.py
elif [ "$FORCE_PROMOTE" = "true" ]; then
  echo "[RETRAIN] gate failed, but FORCE_PROMOTE=true so promoting anyway"
  run_py python promote_model.py --force
else
  echo "[RETRAIN] gate failed; candidate not promoted"
  exit 1
fi

echo "[RETRAIN] complete $(date -u)"
