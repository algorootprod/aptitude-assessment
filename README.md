# Aptitude Assessment

A Python microservice that runs a daily diagnostic aptitude test ("Daily 20" — 4 sections × 5
questions, 25 minutes) for candidates in the AlgoJob stack, and produces a reflective
diagnostic report — what kind of mistake the candidate made and how to fix it, not a score.

**Status: scaffold only.** See [CLAUDE.md](CLAUDE.md) for the full design record — module
responsibilities, the evaluation model, the data model, and what's intentionally not
implemented yet.

## Quick start

```bash
# 1. Install dependencies (Python >= 3.12 + uv)
uv sync

# 2. Configure environment — set DATABASE_URL (Neon) and ASH_SHARED_SECRET
cp .env.example .env

# 3. Run the API — migrations apply automatically on start (RUN_MIGRATIONS=true by default)
./run.sh
# -> http://localhost:8090/docs
```

To run the signup SQS consumer as a separate process instead of `RUN_CONSUMERS=true`:

```bash
./scripts/run_worker.sh signup
```

To manage the ASH API-key pool:

```bash
./scripts/run_worker.sh keys add --provider openai --key sk-...
./scripts/run_worker.sh keys list
```

## Architecture overview

Three modules behind this service's Postgres database:

| Module | Owns |
|---|---|
| `user_topic_mapping` | Per-candidate topic mastery |
| `user_test_mapping` | The assembled test (and, for now, `question_bank`) |
| `evaluation_report` | Scoring, classification, and the rendered report |

See [CLAUDE.md](CLAUDE.md) for the end-to-end flow, the quadrant-classification evaluation
model, and the REST/SQS integration contract with the Nest gateway.

## Tests & quality

```bash
uv run ruff check .
uv run mypy app
uv run pytest tests/unit/ -v
```
