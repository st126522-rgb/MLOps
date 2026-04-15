# Git Workflow

## Day-to-day flow

1. Start from the latest main branch.
2. Create a small feature branch for one task.
3. Run local validation before committing.
4. Open a pull request back into `main`.
5. Merge only after the local pipeline and tests look clean.

## Commands

```powershell
git checkout main
git pull origin main
git checkout -b codex/local-first-setup

python -m pytest -q
python run_local.py --stage all

git status
git add README.md GIT_WORKFLOW.md config.py s3_utils.py ingest.py NER.py drift.py eval.py graph.py run_local.py requirements.txt .gitignore
git commit -m "Add local-first pipeline workflow"
git push -u origin codex/local-first-setup
```

## Branch naming

- Use `codex/<topic>` for implementation work.
- Use `docs/<topic>` for documentation-only updates.
- Use `fix/<topic>` for urgent corrections.

## Commit style

- Keep commits scoped to one change.
- Use imperative messages like `Add local pipeline runner`.
- Avoid mixing generated files with source changes.
