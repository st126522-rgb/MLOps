# AI News NER Pipeline

This repo is easiest to approach in three stages:

1. Run everything locally with `local_data/`.
2. Repeat the same flow on manually created AWS resources.
3. Replace the manual AWS setup with Terraform.

The code now defaults to local mode so you can validate the pipeline before touching cloud infrastructure.
For the practical starting sequence, use [MVP_LAUNCHPAD.md](/C:/Users/gaurav/OneDrive/Desktop/MLOps/MVP_LAUNCHPAD.md).
If Docker, Git, Lambda, or EventBridge are new to you, start with [BEGINNER_DOCKER_AWS_GIT_GUIDE.md](/C:/Users/gaurav/OneDrive/Desktop/MLOps/BEGINNER_DOCKER_AWS_GIT_GUIDE.md).
For the manual cloud setup, use [AWS_MANUAL_ARCHITECTURE_GUIDE.md](/C:/Users/gaurav/OneDrive/Desktop/MLOps/AWS_MANUAL_ARCHITECTURE_GUIDE.md).
For the normal AWS Free Tier Docker path, use [AWS_FREE_TIER_DOCKER_TUTORIAL.md](/C:/Users/gaurav/OneDrive/Desktop/MLOps/AWS_FREE_TIER_DOCKER_TUTORIAL.md).
For the Learner Lab Docker path with Lambda ingest, use [AWS_LEARNER_LAB_DOCKER_TUTORIAL.md](/C:/Users/gaurav/OneDrive/Desktop/MLOps/AWS_LEARNER_LAB_DOCKER_TUTORIAL.md).
For CloudWatch/SNS drift alerts and the human-label retraining loop, use [CLOUDWATCH_SNS_DRIFT_GUIDE.md](/C:/Users/gaurav/OneDrive/Desktop/MLOps/CLOUDWATCH_SNS_DRIFT_GUIDE.md).
For the laptop -> GitHub -> EC2 workflow, use [GIT_WORKFLOW.md](/C:/Users/gaurav/OneDrive/Desktop/MLOps/GIT_WORKFLOW.md).
For the exact learner-lab finish line before Free Tier/Terraform, use [MVP_CLOSURE_CHECKLIST.md](/C:/Users/gaurav/OneDrive/Desktop/MLOps/MVP_CLOSURE_CHECKLIST.md).

## What the pipeline does

The project ingests AI news, extracts named entities, tracks confidence drift, and builds graph-style outputs for inspection.

```text
ingest.py  ->  raw batches
NER.py     ->  entities + drift logs + label queue
drift.py   ->  drift reports + optional alerts
graph.py   ->  HTML graph, timeline, and table views
dashboard.py -> polished graph dashboard with timeframe comparison
backfill_model_entities.py -> upgrades older outputs with MODEL entities
eval.py    ->  F1 metrics for the current or candidate model
label_review.py -> export/import human-reviewed labels
train.py    -> fine-tune a local candidate model
promote_model.py -> promote a passing candidate to current
scripts/run_retrain_cycle_docker.sh -> one-command reviewed-label retrain flow
```

## Repository layout

```text
config.py         Runtime config for local or AWS execution
ingest.py         Fetches RSS articles
NER.py            Runs Hugging Face NER on batches
drift.py          Computes drift metrics from confidence history
graph.py          Builds graph and dashboard-style HTML outputs
dashboard.py      Polished local UI for hot topics and graph comparison
backfill_model_entities.py  Adds MODEL entities to older local outputs
eval.py           Runs evaluation and gate logic
s3_utils.py       Shared storage helpers for local mode and S3
run_local.py      One-command local runner
label_review.py   Label queue review workflow
train.py          Local candidate fine-tuning
promote_model.py  Local model promotion
main.tf           Terraform infrastructure
variables.tf      Terraform inputs
outputs.tf        Terraform outputs
deploy.yml        GitHub Actions workflow draft
GIT_WORKFLOW.md   Suggested branch and commit workflow
scripts/run_retrain_cycle_docker.sh  Docker retrain/eval/promote runner
verify_current_model.py  Verify whether models/current is actually active
```

## Local setup

### 1. Create an environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Run tests

```powershell
python -m pytest -q
```

### 3. Execute the pipeline locally

Run the whole pipeline:

```powershell
python run_local.py --stage all
```

Run individual stages:

```powershell
python run_local.py --stage ingest
python run_local.py --stage ner
python run_local.py --stage backfill-models
python run_local.py --stage drift
python run_local.py --stage graph
python run_local.py --stage dashboard
python run_local.py --stage eval
```

Optional graph auto-open:

```powershell
python run_local.py --stage dashboard --graph-open
```

### 4. Local outputs

Generated files land under `local_data/`:

```text
local_data/raw/
local_data/entities/
local_data/drift/
local_data/label-queue/
local_data/graphs/
local_data/eval/
```

## Polished Local Dashboard

The polished dashboard is the UI artifact to validate before AWS hosting:

```powershell
python run_local.py --stage dashboard --graph-open
```

It generates:

```text
local_data/graphs/dashboard_YYYYMMDD_HHMMSS.html
```

The dashboard includes:

- Hot-topic cards ranked by mentions and confidence.
- Clear type colors for `ORG`, `MODEL`, `MISC`, `PER`, and `LOC`.
- Confidence and flagged-span trends.
- Zoomable, pannable graph panels.
- Two timeframe sliders for comparing graph state across weeks.
- A minimum node-size filter based on mention count.
- A minimum edge-strength filter based on shared article co-mentions.
- A display-labels toggle for decluttering dense views.
- An only-newly-added-nodes toggle for spotting emerging topics.
- Entity-type highlight filters for `ORG`, `MODEL`, `MISC`, `PER`, and `LOC`.
- Edge hover text explaining which nodes are connected and how often.
- New, dropped, and rising entities between the selected timeframes.

## MODEL Entity Type

The base transformer emits `ORG`, `PER`, `LOC`, and `MISC`. This project adds a domain-specific `MODEL` type for AI model and product names such as `GPT-5`, `Claude`, `Gemini`, `DeepSeek`, `Llama`, `Mistral`, `Grok`, `Qwen`, `Sora`, and `DALL-E`.

For new NER runs, [NER.py](/C:/Users/gaurav/OneDrive/Desktop/MLOps/NER.py) adds `MODEL` automatically. For older local entity outputs, run:

```powershell
python run_local.py --stage backfill-models
python run_local.py --stage dashboard --graph-open
```

## Complete Local ML Loop Before AWS

Do not move to manual AWS until this loop works locally.

### 1. Collect low-confidence review candidates

`LABEL_CONFIDENCE_THRESH` is set to `0.85`, so the label queue intentionally captures more uncertain spans for review. Drift still uses the lower `DRIFT_LOW_CONFIDENCE_THRESH = 0.70` so daily novelty does not create noisy drift alerts.

```powershell
python run_local.py --stage all
python label_review.py export --limit 200
```

This writes:

```text
local_data/review/label_review.csv
```

Edit the CSV:

- Use `status=accept` when the suggested entity and type are correct.
- Use `status=correct` and fill `corrected_entity` plus `corrected_type` when the span is useful but wrong.
- Use `status=reject` for junk spans like generic words.
- Keep `split=train` for training examples and `split=eval` for held-out evaluation examples.

### 2. Build local train and eval datasets

```powershell
python label_review.py build-datasets
```

This writes:

```text
local_data/labeled/train_set.json
local_data/eval/test_set.json
```

### 3. Evaluate the current baseline

```powershell
python eval.py --model-prefix models/current --upload-results --current-result
```

If `models/current` does not exist yet, this uses the base Hugging Face model and stores the baseline eval result locally.

### 4. Fine-tune a candidate model

Start small while proving the workflow:

```powershell
python train.py --epochs 1 --max-samples 100 --overwrite
```

This writes:

```text
local_data/models/candidate/
```

### 5. Evaluate the candidate and run the gate

```powershell
python eval.py --model-prefix models/candidate --upload-results --candidate-result
python eval.py --check-gate
```

### 6. Promote only if the gate passes

```powershell
python promote_model.py
```

This copies:

```text
local_data/models/candidate/
```

to:

```text
local_data/models/current/
```

At this point the local loop is complete: ingest, NER, drift, graph, label review, train, eval, and promote all run before AWS.

### Optional one-command Docker retrain flow

After Docker is set up, you can run the reviewed-label loop with one operator script.

First pass, export the CSV and stop for review:

```bash
bash scripts/run_retrain_cycle_docker.sh
```

Then edit:

```text
local_data/review/label_review.csv
```

Then resume the actual train/eval/promote cycle:

```bash
SKIP_EXPORT=true bash scripts/run_retrain_cycle_docker.sh
```

In cloud mode, this script now uploads `models/candidate` and promoted `models/current` back to S3 so future EC2 runs can load the promoted model automatically.

## Stage 2: Manual AWS architecture

After local execution is stable, move to a manually created AWS setup before Terraform.

### Create these resources manually

- 1 private S3 bucket for pipeline data
- 1 EC2 instance for scheduled execution
- 1 IAM role or IAM user with S3 read/write access
- 1 EC2 key pair for SSH access
- 1 security group allowing SSH from your IP

### Set the environment on EC2

```bash
export LOCAL_MODE=false
export S3_BUCKET=your-bucket-name
export AWS_DEFAULT_REGION=us-east-1
```

### Run the same scripts against AWS

```bash
python ingest.py
python NER.py
python drift.py
python eval.py --bucket your-bucket-name --upload-results
```

### Suggested manual validation order

1. Run `ingest.py` and confirm `raw/` files appear in S3.
2. Run `NER.py` and confirm `entities/`, `drift/`, and `label-queue/` appear.
3. Run `drift.py` and confirm `drift/reports/` is written.
4. Run `eval.py` and confirm `eval/` results are stored.

## Stage 3: Terraform

Use Terraform only after the manual AWS version behaves the way you want.

### Prerequisites

- Terraform installed locally
- AWS CLI configured
- An existing EC2 key pair in AWS
- A manually created S3 bucket for Terraform state

### Bootstrap Terraform state bucket

```bash
aws s3 mb s3://ai-news-mlops-tfstate --region us-east-1
aws s3api put-bucket-versioning --bucket ai-news-mlops-tfstate --versioning-configuration Status=Enabled
```

### Create `terraform.tfvars`

```hcl
s3_bucket_name = "your-unique-pipeline-bucket"
key_pair_name  = "your-existing-keypair"
environment    = "dev"
```

### Apply

```bash
terraform init
terraform validate
terraform plan
terraform apply
```

### After apply

Use the outputs in [outputs.tf](/C:/Users/gaurav/OneDrive/Desktop/MLOps/outputs.tf) to SSH into EC2 and confirm the bucket, IP, and dashboard URL.

## Git workflow

The short version is:

1. Pull `main`.
2. Create a branch.
3. Run tests and the local pipeline.
4. Commit only source and docs changes.
5. Push and open a PR.

Full commands are in [GIT_WORKFLOW.md](/C:/Users/gaurav/OneDrive/Desktop/MLOps/GIT_WORKFLOW.md).

## Notes

- `local_data/` and generated HTML outputs are ignored by git.
- The current GitHub Actions file is still a draft and should be aligned with the flat repo layout before relying on it for deployment.
- `eval.py` uses mock data when no eval test set exists yet.
- Local level verification complete. Now testing on AWS
