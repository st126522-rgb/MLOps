# AI News NER Pipeline — Minimal Architecture

## Architecture (S3-only, simplified)

```
[Google RSS + HN API]
        │
        ▼
[Lambda / Cron]  ──── ingest.py ────►  S3: raw/
                                              │
                                              ▼
                                       ner.py (EC2 t2.micro)
                                              │
                          ┌───────────────────┼────────────────────┐
                          ▼                   ▼                    ▼
                   S3: entities/       S3: drift/          S3: label-queue/
                                              │
                                              ▼
                                       drift.py  ──── drift detected? ────►  S3: drift/alerts/
                                                                                       │
                                                         GitHub Actions detects alert  │
                                                                                       ▼
                                                                              retrain.py (SageMaker)
                                                                                       │
                                                                                       ▼
                                                                               S3: models/candidate/
                                                                                       │
                                                                              eval.py F1 gate
                                                                                       │
                                                                    ┌──── pass ────────┴──── fail ────┐
                                                                    ▼                                  ▼
                                                           S3: models/current/              GitHub Issue opened
```

## Setup

### 1. AWS Prerequisites
- AWS account with Free Tier
- IAM user with S3 + EC2 + SageMaker permissions
- EC2 key pair created in AWS Console

### 2. Bootstrap Terraform State Bucket (one-time manual step)
```bash
aws s3 mb s3://ai-news-mlops-tfstate --region us-east-1
aws s3api put-bucket-versioning \
  --bucket ai-news-mlops-tfstate \
  --versioning-configuration Status=Enabled
```

### 3. Configure Terraform
```bash
cd terraform/
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
```

### 4. Deploy Infrastructure
```bash
terraform init
terraform plan
terraform apply
```

### 5. Add GitHub Secrets
In GitHub repo → Settings → Secrets → Actions:
```
AWS_ACCESS_KEY_ID       your IAM key
AWS_SECRET_ACCESS_KEY   your IAM secret
EC2_HOST                output from terraform apply (ec2_public_ip)
EC2_SSH_KEY             contents of your .pem file
S3_BUCKET               your bucket name
```

### 6. Push to main — CI/CD runs automatically
```bash
git add .
git commit -m "initial deployment"
git push origin main
```

## CI/CD Pipeline (4 jobs on every push to main)

| Job | What it does |
|-----|-------------|
| `test` | Runs unit tests — no AWS needed |
| `terraform` | Plans + applies infrastructure changes |
| `deploy` | SSHes to EC2, deploys latest pipeline code |
| `evaluate` | Runs F1 eval gate — blocks deployment if model regresses |

## S3 Folder Structure

```
s3://your-bucket/
  raw/YYYY-Www/           ← raw article JSON (expires 30 days)
  processed/YYYY-Www/     ← cleaned text
  entities/YYYY-Www/      ← NER output per batch
  graphs/YYYY-Www/        ← knowledge graph PNG + JSON
  drift/YYYY-Www/         ← confidence score logs
  drift/reports/          ← rolling drift metric reports
  drift/alerts/           ← written when drift detected
  label-queue/YYYY-Www/   ← low-confidence spans (expires 60 days)
  labeled/YYYY-Www/       ← annotated spans
  models/current/         ← production model weights
  models/candidate/       ← newly fine-tuned model (pre-eval)
  eval/                   ← F1 evaluation results
```
