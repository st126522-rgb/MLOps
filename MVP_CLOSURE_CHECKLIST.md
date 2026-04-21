# MVP Closure Checklist

This is the practical finish line for the learner-lab MVP before moving to Free Tier and Terraform.

The goal is not to make the system perfect.
The goal is to make the system repeatable, explainable, and complete enough that infrastructure codification is worth doing next.

## Definition Of MVP Complete

The MVP is complete when all of these are true:

- Lambda ingest runs on schedule and writes new `raw/` batches to S3.
- EC2 Docker processing can run repeatably and writes `entities/`, `drift/`, `label-queue/`, and `graphs/`.
- CloudWatch receives drift metrics and SNS can notify the human reviewer.
- The human can review `label_review.csv` and build `train_set.json` plus `test_set.json`.
- Retraining runs end to end without manual code edits.
- Eval and gate logic block weak candidates.
- At least one candidate eventually promotes to `models/current/`.
- Inference can prove it is loading `models/current/` instead of always falling back to `dslim/bert-base-NER`.

## Current Status Snapshot

As of the current learner-lab workflow:

- Local pipeline: complete
- Docker build/run: complete
- Lambda ingest: complete
- EventBridge schedule for ingest: complete
- EC2 Docker inference pipeline: complete
- Drift metrics to CloudWatch: complete
- SNS drift email: complete
- Human review CSV flow: complete
- Retrain/eval/gate loop: complete
- Candidate model upload to S3: complete
- Promoted current model in S3: not yet complete
- Proof that EC2 inference uses promoted `models/current`: not yet complete

## Remaining MVP Work

### 1. Grow the reviewed dataset

Target:

```text
Train rows: 80-150 useful accepted/corrected samples
Eval rows: 20-40 useful accepted/corrected samples
```

Why:

- `26` train rows and `13` eval rows are enough to prove the loop.
- They are not enough to expect reliable promotion.
- The classifier head was reinitialized after adding `MODEL`, so it needs more signal.

### 2. Run another retrain cycle with stronger settings

Recommended next run:

```bash
EPOCHS=2 MAX_SAMPLES=150 SKIP_EXPORT=true bash scripts/run_retrain_cycle_docker.sh
```

Why:

- `1` epoch is a smoke test.
- `2` epochs is still lightweight, but gives the new classifier head more chance to learn.

### 3. Get one passing promotion

Success condition:

```text
Candidate F1 > Current F1 + 0.005
```

If this happens:

- `promote_model.py` copies the model to `models/current`
- `models/current` uploads to S3
- the production inference path now has a real promoted model

### 4. Verify inference is using the promoted model

Run:

```bash
docker run --rm \
  --env-file .env \
  -v $(pwd)/local_data:/app/local_data \
  -v ai_news_hf_cache:/cache/huggingface \
  ai-news-mlops:latest \
  python verify_current_model.py
```

Success condition:

```text
[VERIFY] Model source kind : promoted-s3
```

or in local mode:

```text
[VERIFY] Model source kind : promoted-local
```

If it says:

```text
[VERIFY] Model source kind : base-model
```

then there is still no promoted model available.

### 5. Lock the learner-lab cadence

Recommended learner-lab cadence:

- Lambda ingest: every 6 or 12 hours
- EC2 inference/dashboard: manual or every 12 hours if lab persistence allows
- Retraining: manual after reviewed labels are ready

Why:

- Learner Lab time limits make long-lived EC2 automation unreliable.
- Ingest is safe to schedule.
- Retraining should remain readiness-driven, not blind time-driven.

## Operator Runbook

### A. Daily / periodic inference run

On EC2:

```bash
cd /opt/ai-news-mlops/MLOps
sudo systemctl start docker
git pull
RUN_INGEST_IN_EC2=false bash scripts/run_cloud_pipeline_docker.sh
```

Check:

- new files under `raw/`, `entities/`, `drift/`, `graphs/`
- CloudWatch metric updates
- SNS email when drift is triggered

For scheduled EC2 runs with persistent logs:

```bash
RUN_INGEST_IN_EC2=false bash scripts/run_cloud_pipeline_logged.sh
```

This writes:

- timestamped logs under `logs/`
- a latest log copy
- a last-status file
- uploaded copies under `s3://$S3_BUCKET/logs/ec2-processing/`

### B. Human review cycle

Export:

```bash
bash scripts/run_retrain_cycle_docker.sh
```

Review:

- edit `local_data/review/label_review.csv`
- mark rows as `accept`, `correct`, or `reject`

Resume:

```bash
SKIP_EXPORT=true bash scripts/run_retrain_cycle_docker.sh
```

### C. Promotion verification

After a successful retrain:

```bash
docker run --rm \
  --env-file .env \
  -v $(pwd)/local_data:/app/local_data \
  -v ai_news_hf_cache:/cache/huggingface \
  ai-news-mlops:latest \
  python verify_current_model.py
```

## When To Move To Free Tier

Move to Free Tier when these are true:

- the learner-lab inference loop is repeatable
- the reviewed-label retrain loop is repeatable
- at least one candidate has been promoted successfully
- you can explain the whole pipeline without hand-waving

Do not move to Terraform before that.

Terraform should codify a stable architecture, not a still-moving target.

## Free Tier Will Mostly Mirror Learner Lab

The core architecture stays the same:

- EventBridge Scheduler
- Lambda ingest
- S3 data lake
- EC2 Docker ML worker
- CloudWatch drift metrics
- SNS alert
- human review
- retrain/eval/gate/promote

The main difference in Free Tier:

- cleaner IAM roles
- cleaner security-group design
- more deliberate persistence choices
- Terraform-managed roles, policies, alarms, schedules, and compute wiring
