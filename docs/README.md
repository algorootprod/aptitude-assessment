# Module documentation

How the implemented modules actually work, module by module. [`../CLAUDE.md`](../CLAUDE.md) is
the design record — the rules, the decisions and why they were made. These are the walkthroughs:
what runs, in what order, and what the data does to it.

| Module | Status | Doc |
|---|---|---|
| `user_topic_mapping` | built | [user_topic_mapping.md](user_topic_mapping.md) |
| `user_test_mapping` | built | [user_test_mapping.md](user_test_mapping.md) |
| `evaluation_report` | built | [evaluation_report.md](evaluation_report.md) |
| `question_generation` | Phase 2 — directory + README only | — |

## The one-paragraph version

Each candidate holds a **level 1–5 per topic**, seeded at 2. `user_topic_mapping` owns those
levels; `user_test_mapping` owns the question bank; `evaluation_report` owns what happened. On
signup, and again after every evaluation, `user_topic_mapping` picks which topics the next paper
draws from (strict round-robin), then hands `user_test_mapping` a list of `(section, topic, level)`
slots to resolve into concrete questions. The paper is assembled and persisted *before* anyone
asks for it, so `POST /v1/tests/start` is a pure, idempotent read. On submission
`evaluation_report` classifies every answer into a quadrant, returns the report immediately, and
pushes the level ladder and tomorrow's paper into a background task — each topic moving by at
most one level, and only on two consecutive signals in the same direction.

```
POST /v1/users/signup ─┐
                       ├─> user_topic_mapping.handle_user_signup
algoaptitude-user-     ┘        │
signup.fifo (SQS)               │ seed 54 topic rows @ level 2, cycle 1
                                │ select_slots()  ── strict round-robin
                                │ mark last_cycle / times_tested
                                ▼
                       user_test_mapping.on_topic_change(user_id, cycle, slots)
                                │ resolve each slot -> a question id
                                │ persist user_test_questions (ids only, no answers)
                                ▼
POST /v1/tests/start ──> user_test_mapping.get_for_user  (pure read, inlines content)

POST /v1/tests/complete ─> evaluation_report.evaluate
                                │ classify x20 -> build the report
                                │ persist answers, results, report
                                ├─────────────────────> RETURNS the report  (~1.4s)
                                ▼ background
                       user_topic_mapping.update_from_evaluation
                                │ ladder: one outcome per topic -> level move
                                │ cycle_version += 1
                                ├─> on_topic_change again   (tomorrow's paper)
                                └─> publish evaluation-completed

POST /v1/admin/reconcile ─> re-runs the background half if it ever died
```

## Shape of a paper

Four sections, 20 questions, ~21 minutes at level 2 (it lengthens as the candidate levels up —
budgets are computed, not fixed).

| Section | Topics in bank | Topics per paper | Questions |
|---|---|---|---|
| `di` | 3 | **1** — one whole `set_id`, five questions sharing one chart | 5 |
| `quant` | 17 | 5 | 5 |
| `reasoning` | 17 | 5 | 5 |
| `english` | 17 | 5 | 5 |

## What a candidate is told

Never a score. The report names the *kind* of mistake, per question:

| Quadrant | Rule | Meaning |
|---|---|---|
| `mastered` | correct, inside the budget | clean |
| `fragile` | correct, over the budget | knows it, too slow |
| `careless` | wrong, under half the budget | rushed — or skipped with time in hand |
| `gap` | wrong, at or over half the budget | the method isn't there yet |
| `unreached` | the section clock expired first | — |

Findings are patterns across the paper rather than a list of mistakes — two slow answers in one
section mean something, one does not — and every weak point gets a prescription.

## Reading order

Start with [user_topic_mapping.md](user_topic_mapping.md) — it is the entry point for both
signup and evaluation, and it decides what `user_test_mapping` is asked for. Then
[user_test_mapping.md](user_test_mapping.md) for how a slot becomes a question and what the
bank makes hard. Then [evaluation_report.md](evaluation_report.md) for how a submission becomes
a report, and why the pipeline is shaped around latency.
