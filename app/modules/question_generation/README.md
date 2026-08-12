# question_generation — Phase 2

Not built in this pass. Questions are currently curated externally and imported via
`app/workers/import_question_bank.py` from a path to be shared later (the `QuestionBank`
model temporarily lives in `app/modules/user_test_mapping/models.py`).

When this module is designed (~2 months out, per the project timeline), it takes over
ownership of the `question_bank` table — following apex-assessment's convention where the
generating module owns the bank and every other module reads it only through this module's
`service.py`, never through its `repository.py` or `models.py` directly.

See `CLAUDE.md` at the repo root, section "Phase 2", for the full context.
