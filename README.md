# AI News Entity Intelligence Platform

This repository is an end-to-end MLOps project for ingesting AI news, extracting named entities, monitoring drift, reviewing low-confidence predictions, training improved NER models, and promoting better models into production.

The repo is intentionally organized around a simple operating path:

1. Run the full pipeline locally.
2. Run the same pipeline on one AWS EC2 runner with S3 as the artifact store.
3. Recreate the baseline cloud infrastructure with Terraform.

The current GitHub repository is: [https://github.com/st126522-rgb/MLOps](https://github.com/st126522-rgb/MLOps)

## What This Project Does

The pipeline processes AI news articles and produces operational outputs across the full ML lifecycle:

- `ingest.py` fetches RSS news batches.
- `NER.py` runs transformer-based NER and adds a domain-specific `MODEL` entity type.
- `drift.py` tracks confidence drift and low-confidence span rates.
- `graph.py` builds graph-style HTML outputs.
- `dashboard.py` generates a richer comparison dashboard for local or EC2 demo use.
- `label_review.py` exports and re-imports human-reviewed label corrections.
- `train.py` fine-tunes a candidate model.
- `eval.py` evaluates current and candidate models and enforces an F1 gate.
- `promote_model.py` promotes a passing candidate into `models/current`.

## Services And Tooling Used

### Implemented in the codebase today

- `Amazon S3`
  Stores raw articles, extracted entities, drift outputs, label queue files, graphs, models, and eval results.
- `Amazon EC2`
  Runs the Python pipeline in the manual AWS path.
- `IAM role + instance profile`
  Gives the EC2 runner scoped S3 access.
- `AWS CLI`
  Used for bucket checks, artifact sync, uploading reviewed labels, model sync, and Terraform state bootstrap.
- `AWS Systems Manager (SSM)`
  Recommended for Session Manager access and for Run Command scheduling targets in the manual ops flow.
- `Terraform`
  Recreates the baseline infra stack currently defined in `terraform/main.tf`.
- `GitHub Actions`
  Draft CI/CD workflow in `.github/workflows/deploy.yml` for tests, Terraform, EC2 deploy, and evaluation gating.

### Used in the documented manual AWS operating path

- `EventBridge Scheduler`
  Optional cloud-native scheduler for invoking SSM Run Command.
- `CloudWatch`
  Intended for logs, custom drift metrics, and alarms.
- `SNS`
  Intended for simple email alerts.

### Mentioned as later-stage or optional architecture upgrades

- `Step Functions`
  Optional orchestration layer after the single EC2 shell script is stable.
- `DynamoDB`
  Optional query/index layer if the dashboard needs faster cloud lookups.
- `SageMaker`
  Optional future training service; current training flow stays on EC2/local disk.

## Repository Layout

```text
.github/workflows/deploy.yml       Draft GitHub Actions workflow
pipeline/                          Pipeline implementation
  run_local.py                     Local orchestration implementation
  config.py                        Runtime configuration and thresholds
  ingest.py                        RSS ingestion
  NER.py                           NER inference plus MODEL entity enrichment
  entity_postprocess.py            MODEL entity merge logic
  drift.py                         Drift metrics and alert conditions
  graph.py                         HTML graph generation
  dashboard.py                     Static dashboard generation
  label_review.py                  Human review export/import flow
  train.py                         Candidate model fine-tuning
  eval.py                          Evaluation and F1 gate
  promote_model.py                 Candidate-to-current promotion
  backfill_model_entities.py       Upgrade older outputs with MODEL entities
  s3_utils.py                      Local/S3 storage abstraction
  requirements.txt                 Runtime dependencies for deploy jobs
  tests/test_pipeline.py           Offline unit tests
terraform/                         Baseline AWS infrastructure
  main.tf
  variables.tf
  outputs.tf
  terraform.tfvars.example
run_local.py                       Root wrapper for `pipeline/run_local.py`
requirements.txt                   Local dependency install file
docs/                              Manual AWS and Git workflow guides
notebooks/analysis.ipynb           Exploratory notebook
lib/                               Static browser assets for graph outputs
```
## Storage Layout

The pipeline uses the same logical structure locally and in S3:

```text
raw/
processed/
entities/
drift/
label-queue/
labeled/
review/
eval/
graphs/
models/current/
models/candidate/
logs/
```

## Local Quickstart

### 1. Create and activate a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Run tests

```powershell
python -m pytest -q pipeline/tests
```

### 3. Run the full local pipeline

```powershell
python run_local.py --stage all
```

### 4. Run individual stages if needed

```powershell
python run_local.py --stage ingest
python run_local.py --stage ner
python run_local.py --stage backfill-models
python run_local.py --stage drift
python run_local.py --stage graph
python run_local.py --stage dashboard --graph-open
python run_local.py --stage eval
```

### 5. Local output location

Generated artifacts are written under `local_data/`.

## Model Training And Promotion Loop

This repo already supports a full local human-in-the-loop model improvement cycle.

### Export uncertain predictions for review

```powershell
python run_local.py --stage all
python label_review.py export --limit 200
```

This produces:

```text
local_data/review/label_review.csv
```

### Build train and eval datasets

```powershell
python label_review.py build-datasets
```

### Evaluate the current baseline

```powershell
python eval.py --model-prefix models/current --upload-results --current-result
```

### Train a candidate

```powershell
python train.py --epochs 1 --max-samples 100 --overwrite
```

### Evaluate and run the gate

```powershell
python eval.py --model-prefix models/candidate --upload-results --candidate-result
python eval.py --check-gate
```

### Promote a passing model

```powershell
python promote_model.py
```

## AWS Operating Model

The simplest working cloud design is:

- `S3` as the source of truth for artifacts.
- `EC2` as the pipeline runner.
- `IAM role` attached to EC2 for bucket access.
- `SSM Session Manager` for remote shell access without depending on open SSH.
- `SSM Run Command` or `cron` for execution.
- `AWS CLI` for verification, sync, and artifact movement.
- `CloudWatch` and `SNS` as the next monitoring layer.

The detailed step-by-step manual deployment is documented in `docs/AWS_MANUAL_ARCHITECTURE_GUIDE.md`.

## AWS CLI Commands We Actually Use

These are the commands reflected by the current repo and manual guide.

### Verify identity and bucket access

```bash
aws sts get-caller-identity
aws s3 ls s3://$S3_BUCKET/
```

### Bootstrap the Terraform remote state bucket

```bash
aws s3 mb s3://ai-news-mlops-tfstate --region us-east-1
aws s3api put-bucket-versioning --bucket ai-news-mlops-tfstate --versioning-configuration Status=Enabled
```

### Validate pipeline outputs in S3

```bash
aws s3 ls s3://$S3_BUCKET/raw/ --recursive
aws s3 ls s3://$S3_BUCKET/entities/ --recursive
aws s3 ls s3://$S3_BUCKET/drift/ --recursive
aws s3 ls s3://$S3_BUCKET/label-queue/ --recursive
aws s3 ls s3://$S3_BUCKET/drift/reports/ --recursive
```

### Sync data from S3 to EC2 local disk for graph/dashboard generation

```bash
aws s3 sync s3://$S3_BUCKET/raw local_data/raw --quiet
aws s3 sync s3://$S3_BUCKET/entities local_data/entities --quiet
aws s3 sync s3://$S3_BUCKET/drift local_data/drift --quiet
aws s3 sync s3://$S3_BUCKET/label-queue local_data/label-queue --quiet
```

### Upload generated HTML dashboards back to S3

```bash
aws s3 sync local_data/graphs s3://$S3_BUCKET/graphs --exclude "*" --include "*.html" --quiet
```

### Human review CSV round-trip

Download locally:

```powershell
aws s3 cp s3://ai-news-mlops-gaurav-2026/review/label_review.csv .
```

Upload reviewed labels:

```powershell
aws s3 cp .\label_review.csv s3://ai-news-mlops-gaurav-2026/review/label_review.csv
```

### Sync labeled data and promoted models

```bash
aws s3 sync local_data/labeled s3://$S3_BUCKET/labeled
aws s3 sync local_data/eval s3://$S3_BUCKET/eval
aws s3 sync local_data/models/current s3://$S3_BUCKET/models/current --delete
```

## SSM Access And Scheduling

The manual ops path recommends attaching `AmazonSSMManagedInstanceCore` to the EC2 role.
That enables:

- `Session Manager` for shell access without exposing SSH broadly.
- `Run Command` for invoking the pipeline wrapper script remotely.
- `EventBridge Scheduler` as a managed hourly trigger that targets SSM.

Recommended manual scheduling progression:

1. Run the pipeline by hand on EC2.
2. Put the working commands in `/opt/ai-news-mlops/run_cloud_pipeline.sh`.
3. Schedule that script with `cron` first.
4. Move to `EventBridge Scheduler -> SSM Run Command` if you want a cleaner AWS-native demo.
5. Add `Step Functions -> SSM` only if you want explicit workflow orchestration in the final presentation.

## Terraform Stack In This Repo

The current `terraform/main.tf` provisions a baseline stack:

- `1 S3 bucket`
- `S3 public access block`
- `S3 versioning`
- `S3 lifecycle rules for raw/ and label-queue/`
- `placeholder S3 objects for core prefixes`
- `1 IAM role`
- `1 custom S3 access policy`
- `1 IAM instance profile`
- `1 security group`
- `1 Ubuntu EC2 instance`
- `EC2 user_data` that installs Python tooling and writes a simple cron job

### Important current limitation

Terraform does **not** yet fully encode the richer manual ops path described in `docs/AWS_MANUAL_ARCHITECTURE_GUIDE.md`.
In particular, the current Terraform stack does not yet provision:

- `AmazonSSMManagedInstanceCore`
- `CloudWatch` log shipping or alarms
- `SNS` topics/subscriptions
- `EventBridge Scheduler`
- `Step Functions`
- `DynamoDB`
- `SageMaker`

So the README should treat Terraform as the baseline infrastructure recreation path, not the complete final-cloud architecture.

## Recreate The Terraform-Managed Stack From CLI

This is the shortest practical rebuild flow for the infrastructure currently represented in the repo.
It assumes you already have AWS CLI credentials with enough permissions and that Terraform is installed.

### 1. Pick values

```bash
export AWS_DEFAULT_REGION=us-east-1
export TF_STATE_BUCKET=ai-news-mlops-tfstate
export PIPELINE_BUCKET=ai-news-mlops-gaurav-2026
export KEY_PAIR_NAME=ai-news-mlops-key
```

### 2. Create the Terraform state bucket once

```bash
aws s3 mb s3://$TF_STATE_BUCKET --region $AWS_DEFAULT_REGION
aws s3api put-bucket-versioning --bucket $TF_STATE_BUCKET --versioning-configuration Status=Enabled
```

### 3. Create an EC2 key pair if you do not already have one

Linux/macOS:

```bash
aws ec2 create-key-pair --key-name $KEY_PAIR_NAME --query 'KeyMaterial' --output text > ${KEY_PAIR_NAME}.pem
chmod 400 ${KEY_PAIR_NAME}.pem
```

PowerShell:

```powershell
aws ec2 create-key-pair --key-name $env:KEY_PAIR_NAME --query KeyMaterial --output text | Out-File -Encoding ascii "$env:KEY_PAIR_NAME.pem"
```

### 4. Create `terraform.tfvars`

```hcl
s3_bucket_name = "ai-news-mlops-gaurav-2026"
key_pair_name  = "ai-news-mlops-key"
environment    = "dev"
```

### 5. Apply the stack

```bash
terraform init
terraform validate
terraform plan
terraform apply -auto-approve
```

### 6. Read outputs

```bash
terraform output
```

### 7. Destroy when finished

```bash
terraform destroy
```

Note: `terraform destroy` removes only the Terraform-managed resources. If you add manual extras like EventBridge schedules, SNS topics, SSM associations, or CloudWatch alarms outside Terraform, those must be cleaned up separately.

## Suggested EC2 Runtime Environment

For the manual AWS path, the project expects environment variables like:

```bash
export LOCAL_MODE=false
export S3_BUCKET=ai-news-mlops-gaurav-2026
export AWS_DEFAULT_REGION=us-east-1
export LABEL_CONFIDENCE_THRESH=0.85
export DRIFT_LOW_CONFIDENCE_THRESH=0.70
```

## Known Gaps And Honest Notes

- `.github/workflows/deploy.yml` is still a draft and should be validated against your final EC2 operating path before treating it as production CI/CD.
- The Terraform EC2 bootstrap currently installs and schedules only a minimal ingestion path, not the full end-to-end wrapper script from the manual guide.
- The manual guide is more complete operationally than the current Terraform stack.
- `requirements.txt` is intentionally small and may need expansion if you want the full dashboard/training flow on a fresh machine.
- The test suite in `pipeline/tests/test_pipeline.py` is offline-friendly and avoids real AWS credentials.

## Recommended Final Project Story

If this README is being finalized for submission or handoff, the cleanest way to describe the project is:

1. `Local mode` proves the full ML workflow end to end.
2. `Manual AWS mode` proves the same workflow on real cloud infrastructure using S3, EC2, IAM, AWS CLI, and SSM.
3. `Terraform mode` recreates the baseline cloud stack and is the starting point for fully codifying the final AWS architecture.

## Additional Docs

- `docs/AWS_MANUAL_ARCHITECTURE_GUIDE.md` for the detailed manual AWS runbook.
- `docs/GIT_WORKFLOW.md` for repo workflow.
- `terraform/main.tf`, `terraform/variables.tf`, and `terraform/outputs.tf` for infrastructure.
- `.github/workflows/deploy.yml` for the draft CI/CD path.



