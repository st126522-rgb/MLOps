# Git Workflow

This project has three code locations:

```text
Laptop repo
  Where code is edited and tested locally.

GitHub repo
  Shared source of truth.

EC2 repo
  Cloud clone that runs the pipeline.
```

The safe workflow is:

```text
Edit locally
-> test locally
-> commit locally
-> push to GitHub
-> pull on EC2
-> run Docker pipeline
```

Why this matters:

- EC2 cannot run code that only exists on your laptop.
- GitHub gives a recoverable history of every project change.
- Commits make it easier to explain progress in the final report.
- Pulling on EC2 prevents version mismatch problems like old `NER.py` loading an empty model folder.

## Golden Rules

- Do not commit `.env`.
- Do not commit `local_data/`.
- Do not commit AWS credentials.
- Do not commit large generated model files unless explicitly required.
- Prefer editing code on your laptop, then pushing to GitHub.
- Avoid emergency edits directly on EC2 unless you copy them back into Git.

## Step 1: Check Where You Are

Run:

```powershell
git status
git branch
```

Why:

- `git status` shows changed files.
- `git branch` shows the current branch.
- This prevents accidentally committing to the wrong branch.

## Step 2: Create A Work Branch

For implementation work:

```powershell
git checkout main
git pull origin main
git checkout -b codex/docker-aws-guides
```

For documentation-only work:

```powershell
git checkout -b docs/aws-tutorial
```

Why:

- Branches keep `main` clean.
- One branch should represent one logical task.
- It becomes easier to review and merge.

## Step 3: Make Changes Locally

Examples of normal files to edit:

```text
README.md
AWS_FREE_TIER_DOCKER_TUTORIAL.md
AWS_LEARNER_LAB_DOCKER_TUTORIAL.md
BEGINNER_DOCKER_AWS_GIT_GUIDE.md
Dockerfile
lambda_ingest.py
NER.py
scripts/run_cloud_pipeline_docker.sh
```

Why:

- These are source or documentation files.
- They should be versioned.

Files that should normally stay uncommitted:

```text
.env
local_data/
__pycache__/
.pytest_cache/
model.safetensors
*.log
```

Why:

- `.env` is environment-specific.
- `local_data/` is generated data.
- Model files are large artifacts and should live in S3.

## Step 4: Run Local Validation

Run:

```powershell
python -m py_compile NER.py ingest.py lambda_ingest.py s3_utils.py
python -m pytest -q
```

Optional local pipeline check:

```powershell
python run_local.py --stage drift
python run_local.py --stage dashboard
```

Why:

- `py_compile` catches syntax errors.
- `pytest` checks pipeline logic.
- Local validation is faster than debugging on EC2.

Expected:

```text
13 passed
```

The Windows `.pytest_cache` warning is acceptable if tests pass.

## Step 5: Review Changes Before Staging

Run:

```powershell
git status --short
git diff
```

Why:

- `git status --short` lists changed files.
- `git diff` shows actual content changes.
- This catches accidental secrets or generated files.

If `git diff` is too long, inspect one file:

```powershell
git diff -- README.md
```

## Step 6: Stage Only Intended Files

Example for Docker/AWS guide work:

```powershell
git add README.md GIT_WORKFLOW.md BEGINNER_DOCKER_AWS_GIT_GUIDE.md
git add AWS_FREE_TIER_DOCKER_TUTORIAL.md AWS_LEARNER_LAB_DOCKER_TUTORIAL.md
git add Dockerfile .dockerignore docker-compose.yml .env.example
git add lambda_ingest.py scripts/run_cloud_pipeline_docker.sh
git add NER.py
```

Then check:

```powershell
git status --short
```

Why:

- Staging is choosing what goes into the next commit.
- This avoids committing generated files by accident.

## Step 7: Commit

Run:

```powershell
git commit -m "Add beginner Docker AWS workflow"
```

Good commit message style:

```text
Add Lambda ingest entry point
Fix NER fallback for empty current model
Add Docker runner script
Document Learner Lab deployment
```

Why:

- A commit is a checkpoint.
- It can be pushed, reviewed, reverted, or deployed.

## Step 8: Push To GitHub

First push of a new branch:

```powershell
git push -u origin codex/docker-aws-guides
```

Later pushes:

```powershell
git push
```

Why:

- GitHub now has the latest code.
- EC2 can pull it.
- Teammates or instructor can inspect it.

## Step 9: Update EC2

On EC2:

```bash
cd /opt/ai-news-mlops/MLOps
git status
git pull
```

If using a branch:

```bash
git fetch origin
git checkout codex/docker-aws-guides
git pull
```

Why:

- This synchronizes EC2 with GitHub.
- It prevents EC2 from running stale code.

Check files:

```bash
ls Dockerfile
ls lambda_ingest.py
ls scripts/run_cloud_pipeline_docker.sh
```

## Step 10: Rebuild Docker On EC2

After pulling code changes:

```bash
docker build -t ai-news-mlops:latest .
```

Why:

- Pulling code updates files.
- Rebuilding updates the Docker image.
- If you skip rebuild, Docker may run old code from the previous image.

## Step 11: Run The EC2 Docker Pipeline

Before Lambda ingest exists:

```bash
RUN_INGEST_IN_EC2=true scripts/run_cloud_pipeline_docker.sh
```

After Lambda ingest exists:

```bash
RUN_INGEST_IN_EC2=false scripts/run_cloud_pipeline_docker.sh
```

Learner Lab stop-after-run:

```bash
STOP_EC2_AFTER_RUN=true scripts/run_cloud_pipeline_docker.sh
```

Why:

- Before Lambda, EC2 must run ingest itself.
- After Lambda, EC2 only handles heavy ML processing.
- Stopping EC2 protects Learner Lab credits.

## Emergency EC2 Fixes

If you edit a file directly on EC2, copy that fix back into the laptop repo.

Why:

- EC2 local edits disappear from the normal Git workflow.
- The next `git pull` or rebuild can overwrite them.
- Terraform/GitHub will not know about the fix.

Preferred:

```text
Fix on laptop
-> commit
-> push
-> git pull on EC2
```

Emergency only:

```bash
nano NER.py
python -m py_compile NER.py
```

Then manually copy the same fix back to laptop and commit it.

## Branch Naming

Use:

```text
codex/<topic>  implementation work
docs/<topic>   documentation only
fix/<topic>    urgent fixes
```

Examples:

```text
codex/docker-runner
docs/aws-learner-lab-guide
fix/ner-current-model-fallback
```

## Commit Checklist

Before every commit:

```text
git status checked
tests passed
no .env staged
no local_data staged
no AWS keys staged
commit message is clear
```

Before running on EC2:

```text
code pushed to GitHub
EC2 git pull completed
Docker image rebuilt
.env exists on EC2
S3 bucket access works
```

