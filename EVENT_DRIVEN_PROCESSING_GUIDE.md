# Event-Driven Processing and Demo Orchestration

This MVP keeps ingest lightweight on Lambda and runs the heavier NER, drift,
graph, dashboard, and metric publishing stages on EC2 through Docker.

## What was added

- `.github/workflows/ci.yml`
  - Runs Python validation and unit tests on push / pull request.
  - Builds the Docker image to verify deployability.
- `lambda_process.py`
  - S3-trigger-compatible Lambda that starts the EC2 processing script through
    AWS Systems Manager Run Command.
  - Supports `manual_run=true` so Step Functions can start the processing path
    for demos.
- `stepfunctions/inference_pipeline.asl.json`
  - Optional Step Functions state machine definition for a visible demo flow:
    ingest Lambda, then processing Lambda.

## EC2 requirement

The EC2 instance must be managed by AWS Systems Manager.

Minimum requirements:

- SSM Agent running on EC2.
- EC2 instance profile allows SSM managed instance registration.
- Processing Lambda role can call `ssm:SendCommand`.
- EC2 instance has Docker, AWS CLI, repo, `.env`, and scripts available.

Recommended EC2 tag:

```text
Role=ai-news-mlops-processor
```

## Processing Lambda environment

Use these environment variables:

```text
S3_BUCKET=ai-news-mlops-2026
PROCESS_TARGET_TAG_KEY=Role
PROCESS_TARGET_TAG_VALUE=ai-news-mlops-processor
PROCESS_APP_DIR=/opt/ai-news-mlops/MLOps
```

If targeting one instance directly instead of tag-based targeting:

```text
PROCESS_INSTANCE_ID=i-xxxxxxxxxxxxxxxxx
```

## Default EC2 command

If `PROCESS_COMMAND` is not set, the Lambda runs:

```bash
cd /opt/ai-news-mlops/MLOps && RUN_INGEST_IN_EC2=false /bin/bash scripts/run_cloud_pipeline_logged.sh
```

## S3 event trigger

Configure S3 ObjectCreated events for:

```text
prefix: raw/
suffix: .json
```

Target Lambda:

```text
ai-news-mlops-process
```

This creates the automation path:

```text
Lambda ingest -> S3 raw JSON -> processing Lambda -> SSM Run Command -> EC2 Docker pipeline
```

## Step Functions demo path

The optional Step Functions definition runs:

```text
RunIngestLambda -> StartEc2Processing
```

This is useful for a presentation because it makes the orchestration visible.
The processing Lambda receives:

```json
{"manual_run": true, "source": "step-functions"}
```

## Demo talking point

The project now has a lightweight CI layer and a production-shaped automation
path. A push validates tests and Docker build, while new raw data can trigger
downstream NER, drift monitoring, graph refresh, and dashboard updates through
the existing EC2 pipeline.
