#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ai-news-mlops/MLOps}"
IMAGE_NAME="${IMAGE_NAME:-ai-news-mlops:latest}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
LOCAL_DATA_DIR="${LOCAL_DATA_DIR:-$APP_DIR/local_data}"

cd "$APP_DIR"
mkdir -p "$LOCAL_DATA_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  echo "Create it from .env.example and set S3_BUCKET."
  exit 2
fi

set -a
source "$ENV_FILE"
set +a

if [ -z "${S3_BUCKET:-}" ]; then
  echo "[ERROR] S3_BUCKET is not set in $ENV_FILE"
  exit 2
fi

echo "[PIPELINE] start $(date -u)"

docker build -t "$IMAGE_NAME" .

run_py() {
  docker run --rm \
    --env-file "$ENV_FILE" \
    -v "$LOCAL_DATA_DIR:/app/local_data" \
    -v ai_news_hf_cache:/cache/huggingface \
    "$IMAGE_NAME" "$@"
}

# If ingest is moved to Lambda, keep this disabled. Set RUN_INGEST_IN_EC2=true
# only when testing without Lambda.
if [ "${RUN_INGEST_IN_EC2:-false}" = "true" ]; then
  run_py python ingest.py
fi

run_py python NER.py

set +e
run_py python drift.py
DRIFT_STATUS=$?
set -e

if [ "$DRIFT_STATUS" -gt 1 ]; then
  echo "[PIPELINE] drift.py failed unexpectedly with code $DRIFT_STATUS"
  exit "$DRIFT_STATUS"
fi

aws s3 sync "s3://$S3_BUCKET/raw" "$LOCAL_DATA_DIR/raw" --quiet
aws s3 sync "s3://$S3_BUCKET/entities" "$LOCAL_DATA_DIR/entities" --quiet
aws s3 sync "s3://$S3_BUCKET/drift" "$LOCAL_DATA_DIR/drift" --quiet
aws s3 sync "s3://$S3_BUCKET/label-queue" "$LOCAL_DATA_DIR/label-queue" --quiet

run_py python graph.py --dir local_data
run_py python dashboard.py --dir local_data

aws s3 sync "$LOCAL_DATA_DIR/graphs" "s3://$S3_BUCKET/graphs" --exclude "*" --include "*.html" --quiet

if [ "$DRIFT_STATUS" -eq 1 ]; then
  echo "[PIPELINE] drift detected; check s3://$S3_BUCKET/drift/alerts/"
else
  echo "[PIPELINE] no drift detected"
fi

if [ "${STOP_EC2_AFTER_RUN:-false}" = "true" ]; then
  echo "[PIPELINE] stopping EC2 instance after run"
  sudo shutdown -h now
fi

echo "[PIPELINE] complete $(date -u)"
