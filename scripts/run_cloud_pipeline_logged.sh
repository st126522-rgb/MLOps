#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ai-news-mlops/MLOps}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
PIPELINE_SCRIPT="${PIPELINE_SCRIPT:-$APP_DIR/scripts/run_cloud_pipeline_docker.sh}"

cd "$APP_DIR"
mkdir -p "$LOG_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 2
fi

set -a
source "$ENV_FILE"
set +a

if [ -z "${S3_BUCKET:-}" ]; then
  echo "[ERROR] S3_BUCKET is not set in $ENV_FILE"
  exit 2
fi

TIMESTAMP_UTC="$(date -u +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/ec2_pipeline_${TIMESTAMP_UTC}.log"
LATEST_LOG="$LOG_DIR/ec2_pipeline_latest.log"
STATUS_FILE="$LOG_DIR/ec2_pipeline_last_status.txt"
RUN_STATUS="failure"

finalize() {
  local exit_code=$?
  cp "$LOG_FILE" "$LATEST_LOG" 2>/dev/null || true
  printf "%s %s\n" "$RUN_STATUS" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_FILE"
  aws s3 cp "$LOG_FILE" "s3://$S3_BUCKET/logs/ec2-processing/ec2_pipeline_${TIMESTAMP_UTC}.log" --quiet || true
  aws s3 cp "$LATEST_LOG" "s3://$S3_BUCKET/logs/ec2-processing/ec2_pipeline_latest.log" --quiet || true
  aws s3 cp "$STATUS_FILE" "s3://$S3_BUCKET/logs/ec2-processing/ec2_pipeline_last_status.txt" --quiet || true
  echo "[SCHEDULED] uploaded logs to s3://$S3_BUCKET/logs/ec2-processing/" | tee -a "$LOG_FILE"
  exit "$exit_code"
}

trap finalize EXIT

{
  echo "[SCHEDULED] start $(date -u)"
  echo "[SCHEDULED] host $(hostname)"
  echo "[SCHEDULED] app_dir $APP_DIR"
  echo "[SCHEDULED] pipeline_script $PIPELINE_SCRIPT"
  echo "[SCHEDULED] run_ingest_in_ec2 ${RUN_INGEST_IN_EC2:-false}"
  git rev-parse --short HEAD 2>/dev/null | awk '{print "[SCHEDULED] git_rev "$0}'
  /bin/bash "$PIPELINE_SCRIPT"
  echo "[SCHEDULED] status success"
  echo "[SCHEDULED] end $(date -u)"
} 2>&1 | tee "$LOG_FILE"
RUN_STATUS="success"
