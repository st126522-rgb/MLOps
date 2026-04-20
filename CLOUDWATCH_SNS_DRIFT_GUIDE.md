# CloudWatch + SNS Drift Alert Guide

This is the next layer after the EC2 Docker pipeline works.

Goal:

```text
drift.py writes drift report
-> publish_drift_metrics.py sends metrics to CloudWatch
-> CloudWatch alarm watches DriftDetected
-> SNS emails you to review labels
-> human labels
-> train/eval/promote
```

This completes the MVP closed loop without unsafe automatic retraining.

## Critical Scheduling Opinion

Do not run the full EC2 Docker pipeline after every Lambda ingest.

Recommended MVP cadence:

```text
Lambda ingest: every 6-12 hours
EC2 Docker processing: every 12 hours or manual
CloudWatch alarm: reacts whenever processing publishes DriftDetected=1
```

Why:

- Lambda ingest is cheap and light.
- NER is heavy and slower.
- Running NER after every ingest duplicates work and burns Learner Lab credits.
- Drift does not need minute-level reaction for this project.

Use short schedules only for testing:

```text
Lambda ingest smoke test: rate(5 minutes), then disable or change back.
EC2 processing smoke test: run manually.
```

Good final MVP schedules:

```text
Conservative: Lambda every 12 hours, EC2 processing manual once per day.
Balanced: Lambda every 6 hours, EC2 processing every 12 hours.
Demo mode: Lambda every 1 hour, EC2 processing manual before presentation.
```

## What The New Script Publishes

`publish_drift_metrics.py` reads the latest JSON report under:

```text
s3://ai-news-mlops-2026/drift/reports/
```

It publishes these CloudWatch metrics:

```text
Namespace: AI/NewsNER

MeanConfidence
FlaggedSpanPercentage
LabelQueueSize
DriftDetected
```

For MVP alerting, use:

```text
DriftDetected >= 1
```

Why:

- `drift.py` already combines the model-confidence logic and queue-size requirement.
- One alarm is easier to debug than a composite alarm.
- Later we can add separate alarms for mean confidence and flagged percentage.

## Step 1: Pull And Rebuild On EC2

After pushing this repo update:

```bash
cd /opt/ai-news-mlops/MLOps
git pull
docker build -t ai-news-mlops:latest .
```

Run a dry check:

```bash
docker run --rm \
  --env-file .env \
  -v $(pwd)/local_data:/app/local_data \
  -v ai_news_hf_cache:/cache/huggingface \
  ai-news-mlops:latest \
  python publish_drift_metrics.py --dry-run
```

Expected:

```text
[METRICS] Latest report: drift/reports/...
MeanConfidence: ...
FlaggedSpanPercentage: ...
LabelQueueSize: ...
DriftDetected: 0 or 1
```

## Step 2: Run Full Pipeline

```bash
RUN_INGEST_IN_EC2=false bash scripts/run_cloud_pipeline_docker.sh
```

Expected new line:

```text
[METRICS] Published to CloudWatch namespace: AI/NewsNER
```

If it says it cannot publish metrics:

- Free Tier: add `cloudwatch:PutMetricData` to the EC2 role.
- Learner Lab: check whether `LabRole` allows CloudWatch metric publishing.
- If blocked in Learner Lab, keep the S3 drift report and document the limitation.

## Step 3: Create SNS Topic

AWS Console:

```text
SNS -> Topics -> Create topic
Type: Standard
Name: ai-news-mlops-drift-alerts
```

Create subscription:

```text
Protocol: Email
Endpoint: your email
```

Confirm the email subscription.

Why:

- CloudWatch alarm needs a notification target.
- SNS is the simplest AWS-native fan-out service.

## Step 4: Create CloudWatch Alarm

AWS Console:

```text
CloudWatch -> Alarms -> Create alarm
Select metric
Custom namespaces
AI/NewsNER
Metric: DriftDetected
```

Alarm condition:

```text
Statistic: Maximum
Period: 5 minutes for testing, 1 hour for normal
Threshold type: Static
Condition: Greater/Equal >= 1
Datapoints to alarm: 1 out of 1
Missing data: Treat missing data as not breaching
```

Notification:

```text
In alarm -> SNS topic -> ai-news-mlops-drift-alerts
```

Name:

```text
ai-news-mlops-drift-detected
```

Why:

- `Maximum` catches any `1` published in the period.
- Missing data should not alarm because metrics only publish when EC2 processing runs.
- SNS email tells the human to label/retrain.

## Step 5: Testing The Alarm

First confirm metrics are visible:

```bash
aws cloudwatch list-metrics \
  --namespace AI/NewsNER \
  --metric-name DriftDetected
```

If you need to force an alarm test, publish a one-time test metric:

```bash
aws cloudwatch put-metric-data \
  --namespace AI/NewsNER \
  --metric-name DriftDetected \
  --value 1 \
  --unit Count
```

Then wait for the alarm period and check email.

After the test, publish reset value:

```bash
aws cloudwatch put-metric-data \
  --namespace AI/NewsNER \
  --metric-name DriftDetected \
  --value 0 \
  --unit Count
```

For testing only, use:

```text
Period: 5 minutes
```

For normal MVP:

```text
Period: 1 hour
```

## Step 6: Human Labeling After SNS Email

When SNS emails you:

```text
Drift detected. Review label queue and retrain candidate model.
```

Run on EC2:

```bash
cd /opt/ai-news-mlops/MLOps
aws s3 sync s3://$S3_BUCKET/label-queue local_data/label-queue --quiet

docker run --rm \
  --env-file .env \
  -v $(pwd)/local_data:/app/local_data \
  ai-news-mlops:latest \
  python label_review.py export --limit 200
```

Review:

```text
local_data/review/label_review.csv
```

Use:

```text
status=accept
status=correct
status=reject
split=train
split=eval
```

Then:

```bash
docker run --rm --env-file .env -v $(pwd)/local_data:/app/local_data ai-news-mlops:latest python label_review.py build-datasets
```

## Step 7: Retrain, Evaluate, Promote

```bash
docker run --rm \
  --env-file .env \
  -v $(pwd)/local_data:/app/local_data \
  -v ai_news_hf_cache:/cache/huggingface \
  ai-news-mlops:latest \
  python train.py --epochs 1 --max-samples 100 --overwrite
```

Evaluate:

```bash
docker run --rm --env-file .env -v $(pwd)/local_data:/app/local_data -v ai_news_hf_cache:/cache/huggingface ai-news-mlops:latest python eval.py --model-prefix models/current --upload-results --current-result
docker run --rm --env-file .env -v $(pwd)/local_data:/app/local_data -v ai_news_hf_cache:/cache/huggingface ai-news-mlops:latest python eval.py --model-prefix models/candidate --upload-results --candidate-result
docker run --rm --env-file .env -v $(pwd)/local_data:/app/local_data -v ai_news_hf_cache:/cache/huggingface ai-news-mlops:latest python eval.py --check-gate
```

Promote only if gate passes:

```bash
docker run --rm \
  --env-file .env \
  -v $(pwd)/local_data:/app/local_data \
  ai-news-mlops:latest \
  python promote_model.py

aws s3 sync local_data/models/current s3://$S3_BUCKET/models/current --delete
aws s3 sync local_data/eval s3://$S3_BUCKET/eval
```

## MVP Closed Loop Definition

The loop is complete when:

```text
Lambda ingests news on schedule.
EC2 Docker processes raw data.
drift.py creates drift report.
publish_drift_metrics.py publishes DriftDetected.
CloudWatch alarm sends SNS email.
Human reviews label queue.
Candidate model is trained.
Eval gate decides pass/fail.
Passing candidate is promoted to models/current.
Next NER run loads models/current.
```

