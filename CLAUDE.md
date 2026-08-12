# Aptitude Assessment — Claude Code Guide

Aptitude Assessment is a Python microservice that runs a daily diagnostic aptitude test
("Daily 20" — 4 sections × 5 questions, 25 minutes) for candidates in the AlgoJob stack. Its
real product is not a score — it's a **reflective diagnostic report**: what *kind* of mistake
the candidate made (knowledge gap vs. carelessness vs. a slow-but-correct method) and what to
do about it. It integrates with the Backend (NestJS, `algojob_nest`, :5001) via REST (sync, for
signup / test start / test complete / report reads) and SQS (async, for signup fan-out). It
owns its own Postgres database, hosted on Neon.

**Status: the whole loop is built and verified end-to-end against the real Neon database.** A
candidate signs up, receives an adaptive 20-question paper, submits it, gets the diagnostic
report back in the same response, and their per-topic levels move. Only `question_generation`
remains — Phase 2, directory + README only.

**Per-module walkthroughs live in [`docs/`](docs/)** — one file per built module, tracing it call
by call with the data that shaped it. This file stays the design record and the rules; those are
the "how does this actually work" reads.

This service was scaffolded from `apex-assessment`'s conventions (its `CLAUDE.md` is the
canonical reference for the pattern this file follows). Do not use `debug-assessment` as a
model — it's the older flat/Mongo/`os.getenv` style apex's conventions exist to replace.

## The three modules

| Module | Status | Triggered by | Triggers / responds to |
|---|---|---|---|
| `user_topic_mapping` | **built** ([docs](docs/user_topic_mapping.md)) | `POST /v1/users/signup`; `algoaptitude-user-signup.fifo` SQS; sync call from `evaluation_report` | Owns each topic's **level** and the candidate's **`cycle_version`**. Picks the topic slots, then sync-triggers `user_test_mapping.on_topic_change` on every signup and every evaluation |
| `user_test_mapping` | **built** ([docs](docs/user_test_mapping.md)) | `user_topic_mapping` on (a) signup, (b) post-evaluation ladder update | Owns `question_bank` + `user_test_questions`. Resolves topic slots to questions; serves `POST /v1/tests/start` with full content inline and no answer keys |
| `evaluation_report` | **built** ([docs](docs/evaluation_report.md)) | `POST /v1/tests/complete` (REST) | Owns `user_answers`, `evaluation_result`, `user_reports`. Classifies, returns the report **in the response**, then backgrounds the sync+retry call to `user_topic_mapping.update_from_evaluation`. Serves `GET /v1/reports/{user_id}` and `POST /v1/admin/reconcile` |
| `question_generation` | Phase 2 | — | Not built. Directory + README only. |

## End-to-end flow

1. Nest calls `POST /v1/users/signup` (and/or publishes `algoaptitude-user-signup.fifo`) →
   `user_topic_mapping.handle_user_signup`
2. `user_topic_mapping` seeds one `UserTopicMastery` row per topic (54 today) at
   `INITIAL_TOPIC_LEVEL`, picks this cycle's topics by strict round-robin, marks them scheduled,
   then **sync**-calls `user_test_mapping.on_topic_change` (same session) — the diagram's
   "on new user signup" arrow
3. `user_test_mapping` resolves each topic slot to a concrete question from `question_bank` and
   stores `UserTestQuestions` (ids + selection provenance only — no question content, no answers)
4. Nest calls `POST /v1/tests/start` with `{"user_id": ...}` → a pure, idempotent read of the
   pre-assembled test, full question content inline, no answer keys
5. Candidate completes the test → Nest calls `POST /v1/tests/complete` with per-question
   `picked` / `elapsed_seconds` / `unreached`
6. `EvaluationReportService.evaluate(payload)` — **on the request path, three reads and three
   bulk writes, nothing else:**
   - short-circuit if this cycle already has a report (a resubmit returns the same one)
   - read the assembled test and the questions' answer keys, classify all 20 via
     `scoring.classify()`, build the report via `report.py` — all pure between the two reads
   - persist `user_answers`, `evaluation_result` and `user_reports` in one transaction
   - **return the full `ReportOut` to Nest**
7. Everything the *next* paper needs is spawned as a background task (`app/core/tasks.py`):
   `update_from_evaluation` → `user_topic_mapping` → which internally sync-calls
   `user_test_mapping.on_topic_change` again — the diagram's "after every evaluation" arrow —
   then publishes `evaluation-completed`. Retried 2–3 times on its own session.
8. If that task never completes, `POST /v1/admin/reconcile` re-runs it (per-user or as a sweep)
9. Nest calls `GET /v1/reports/{user_id}` → the stored report, optionally `?cycle_version=N`

> **Why this inverts the ordering the scaffold specified.** The original had step 7 run
> *synchronously* and the report built in the background, so the candidate waited ~20 extra Neon
> round-trips for work that only matters when they next sit down, and then waited again for the
> thing they came for. Nothing needs the next paper until `/v1/tests/start` is called, so it
> moved off the request path. The trade is explicit and reconcile is the answer to it.

## Architecture rules (carried over from apex-assessment)

- **Single DB, logical isolation.** One Postgres instance. Each module owns a fixed set of
  tables and is the **only writer** via its own `repository.py`. Cross-module access goes
  through the owning module's `service.py` — never via another module's `repository.py` or
  `models.py` directly.
- **`cycle_version` (per-candidate integer)** is carried on every pipeline row
  (`user_topic_map`, `user_test_questions`, `user_answers`, `evaluation_result`,
  `user_reports`). Born at signup as `1`, increments after each evaluation cycle. Purpose:
  failure recovery — a failed step for user U at a given `cycle_version` can be replayed
  without corrupting later cycles.
- **Idempotency.** Writes should use `ON CONFLICT DO NOTHING` (or `DO UPDATE` where a row is
  meant to be refreshed) keyed on the natural cycle key — e.g. `(user_id, cycle_version)` or
  `(user_id, topic)`. Re-processing the same payload must be safe.
- **Call-type discipline.**
  - `evaluation_report → user_topic_mapping`: **sync + retry 2–3** via
    `infrastructure/messaging/retry.py:retry_sync_call()` — but run from a *background* task, on
    its own session, not on the request path. A fresh session per retry attempt: a failed
    transaction poisons its session, so retrying on the same one fails identically.
  - `user_topic_mapping → user_test_mapping`: **sync**, same session (required so the caller
    can read back the updated test map before its own call returns).
  - Anything fire-and-forget goes through **`app/core/tasks.py:spawn`**, never a bare
    `asyncio.create_task` — asyncio holds only weak references to tasks, so an unreferenced one
    can be garbage-collected mid-flight. `spawn` keeps the reference, logs what the task raises
    (nothing is awaiting it, so an unlogged exception vanishes), and lets shutdown `drain()` it.
  - **Anything backgrounded must be recoverable if it never runs.** `POST /v1/admin/reconcile`
    is that recovery path; `cycle_version` and the ladder's replay guard are what make re-running
    safe.
- **SQS envelope.** All published/consumed messages use
  `{event, version, occurred_at, payload}` (`infrastructure/messaging/publisher.py:build_envelope`).
  This is the same shape Nest's own `buildEnvelope()` produces
  (`algojob_nest/src/algoapex/services/algoapex-producer.service.ts`) for the analogous apex
  queues. FIFO `MessageGroupId` = `payload["user_id"]`.
- **No new top-level files.** New domain features land inside `app/modules/<name>/` following
  the standard layout (`models.py`, `schemas.py`, `repository.py`, `service.py`, optionally
  `handlers.py` for SQS entrypoints).
- **Decision logic goes in a pure module, not the service.** The two built modules each keep
  their rules in dependency-free files the service calls into — `ladder.py` + `rotation.py` in
  `user_topic_mapping`, `selection.py` in `user_test_mapping`. No session, no I/O, no ORM
  instances (they take `Protocol`-typed stand-ins), so the rules that actually decide a
  candidate's level and paper are unit-testable without a database. `evaluation_report` follows
  it too: `scoring.py` and `report.py` are pure, and carry 76 of the suite's 140 tests.

## Evaluation model (from `daily20_prototype.html`)

Implemented in `evaluation_report/scoring.py` and `report.py`; every threshold is a named
constant in `app/core/constants.py`. The quadrant classifier is the heart of the report, ported
from the prototype's `quadOf()`:

| Quadrant | Rule | Meaning |
|---|---|---|
| `mastered` | correct, `elapsed_seconds <= expected_time_seconds` | clean |
| `fragile` | correct, `elapsed_seconds > expected_time_seconds` | knows it, too slow |
| `careless` | wrong, `elapsed_seconds < 0.5 × expected_time_seconds` | rushed, not a knowledge gap |
| `gap` | wrong, `elapsed_seconds >= 0.5 × expected_time_seconds` | the method isn't there yet |
| `unreached` | section clock expired before the question was reached | — |

Two rules the table doesn't show, both from `quadOf()`:
- **A skip is `careless`, not `gap`.** Leaving a question blank with time still on the clock is
  the same failure as answering too fast. Only a genuinely unreached question is `unreached`.
- **A missing answer key can never score correct.** `question_bank.answer` is nullable, and an
  unscoreable question must not read as `mastered`.

**Findings** (patterns across the whole paper, not per-question noise), ported from the
prototype's `report()`:
- `fragile >= 2` questions in one section — right method, too slow.
- `careless >= 2` in a section while less than 75% of its time budget was used — rushed with
  time in hand.
- The same `prerequisite_concept` behind `>= 2` "gap" questions across the whole paper — one
  root cause, not two separate mistakes.
- A roll-up of all `mastered` questions, naming up to four topics.
- If none fire, the report says "Nothing stands out" rather than inventing a pattern.

**Actions** (priority-sorted, deduped by heading, capped at 6):
- `gap` → "Relearn {prerequisite_concept}", detail = the question's `explanation`. The column is
  nullable, so it falls back to the topic rather than printing "Relearn None".
- `fragile` → the question's own `shortcut_name` / `shortcut_how`, **skipped entirely when there
  is no shortcut on file** — which is 70% of the bank, and 87% of English. For the same reason
  the "each of these has a shortcut you are not using" line is only printed when it is true.
- `careless` → pacing advice ("give each question its full budget"), emitted once however many.

**Dropped from the prototype:** its closing line, "Tomorrow's set will over-sample your weaker
topics." Rotation is strict round-robin and ignores performance — only a topic's *level* moves —
so shipping that line would promise something the service does not do.

**The report never leads with a score**, per the prototype's own comment. The one score that
exists anywhere is the DI section's 0–100, and it is an internal ladder signal that is never
shown: see "The level ladder" below.

**Section budgets are computed, not fixed.** The prototype's fixed shape (DI 480s · Quant 360s ·
Reasoning 360s · English 300s = 25:00) no longer holds, because a section's questions now move
with the candidate's per-topic levels. `budget_seconds = ceil(sum(expected_time_seconds) ×
TIME_BUDGET_SLACK)`, computed at assembly time in `user_test_mapping.service._budget`. A fresh
level-2 candidate's paper runs ~21 minutes; it lengthens as they level up.

**Timer lives in Node.** This service never runs a clock — `POST /v1/tests/complete` receives
per-question `elapsed_seconds` and `unreached` and treats them as fact.

## The level ladder (`user_topic_mapping` + `user_test_mapping`)

Both modules are **built**. This is the design they implement.

Each candidate holds a **level 1–5 per topic**, seeded at `INITIAL_TOPIC_LEVEL` (2). `level` maps
1:1 onto `question_bank.difficulty` — no separate column. A level never moves on one result: it
takes two consecutive signals in the same direction, with a **probation** (`pending_dir`) in
between, and a contradictory signal cancels the probation back to neutral rather than flipping it.

| `pending_dir` | signal | result |
|---|---|---|
| any | `0` | unchanged — the probation survives, which is what lets it outlive the ~4 cycles rotation takes to revisit a topic |
| `0` | `±1` | open a probation in that direction |
| `+1` | `+1` | **level + 1**, probation cleared (level 5 caps: level holds, probation still consumed) |
| `-1` | `-1` | **level − 1**, probation cleared (level 1 floors the same way) |
| `+1` | `-1` | cancel to `0` — never flips straight to the opposite probation |
| `-1` | `+1` | cancel to `0` |

**Where the signal comes from is section-dependent** — `app/modules/user_topic_mapping/ladder.py`:

- **Non-DI** (quant/reasoning/english, one question per topic): the quadrant.
  `mastered → +1`, `gap → −1`, and `fragile`/`careless`/`unreached → 0`. Right-but-slow and
  wrong-but-rushed are evidence about *pacing*, not about level.
- **DI** (one topic, five questions): a **0–100 score**, banded strictly —
  `> DI_PROMOTE_SCORE_THRESHOLD (85) → +1`, `< DI_DEMOTE_SCORE_THRESHOLD (40) → −1`, else `0`.
  **This module never computes that score**; `evaluation_report.scoring.di_section_score` does,
  by averaging `QUADRANT_MASTERY_SCORE` over the set's five questions (mastered 100 / fragile 50
  / careless 0 / gap 0 / unreached 0). That keeps speed in the signal: five correct-but-slow
  answers score 50 and hold the level rather than promoting it.

**`QUADRANT_MASTERY_SCORE` is the app's single quadrant score**, and the same table is averaged
in all three places a 0–100 number appears: the DI ladder signal above, `user_topic_map.
mastery_score`, and the report's per-section score card in `dev-ui`. It is the prototype's own
`(mastered + 0.5 × fragile) / n` expressed as a table. `careless` earns **0** because
`scoring.classify()` files a *deliberate skip* under that quadrant — crediting it would let
skipping a question outscore attempting it and getting it wrong, contradicting the paper's "a
blank scores the same as a wrong answer".

The cost of one shared scale: **DI cannot separate `careless` from `gap`** (both 0, so no
threshold can tell them apart), and so demotes on a careless-heavy set where a non-DI section
would hold — non-DI ladders on `QUADRANT_SIGNAL`, which keeps `careless` neutral. That is
tolerable only while DI's ladder is inert for want of uniform-difficulty sets (below); if the
bank ever grows those, give DI its own signal function rather than re-splitting the scale.
`tests/unit/test_scoring.py` pins both bands this moved: `3 mastered + 2 fragile` now holds at
80 (was 88, promote), and `1 mastered + 4 careless` now demotes at 20 (was 44, hold).

`EvaluationResultIn` therefore carries one row per *topic*, with two optional fields and a
validator enforcing exactly one: `{section, topic, quadrant?, score?}` — `score` required for
`di`, `quadrant` required for everything else.

**Topic rotation is strict round-robin and blind to probation.** Order a section's topics by
`(last_cycle, topic)` and take the first N — no cursor column needed, since a never-scheduled
topic sits at `last_cycle = 0`. N is 5 for the 17-topic sections and **1 for DI**, because a DI
section is one whole `set_id`. 17 topics ÷ 5 slots means a probation opened in cycle N resolves
in cycle N+4 (N+3 for DI). That latency is accepted, not an oversight.

**Question selection falls back, and that is the normal path** —
`app/modules/user_test_mapping/selection.py`. The median topic holds one L1 question and zero or
one L5, so an exact-level lookup misses routinely:
`exact → adjacent (L±1) → any_level → [DI only: any_topic] → repeat`. Every slot records which
step produced it in `user_test_questions.sections`, so a paper that drifted off-level is visible
rather than silent. Repeats count toward the ladder normally.

**DI's level ladder is currently inert, by data not by design.** No DI set has a uniform
difficulty — all 60 are 1→4 ramps — so every Bar Charts and Table Charts set means level 3 and
most Pie Charts sets mean level 2. Asking for a level-4 DI set is unsatisfiable and degrades to
"next unseen set in this topic". The ladder still tracks and updates DI levels so the data exists
when the bank grows.

**Two invariants that were bugs before they were invariants:**

- **Assembly is idempotent but rotation is not.** `on_topic_change` returns an already-assembled
  cycle unchanged, but `_assemble_next_test` must *also* check `has_test_for_cycle` before
  selecting slots — otherwise a signup arriving over both REST and SQS (the expected case)
  advances `last_cycle` twice and the candidate's first paper silently skips five topics.
- **Replay guard on `update_from_evaluation`.** An evaluation for a cycle older than the
  candidate's current one is a no-op returning the current snapshot; a *newer* one raises. This
  is what makes `retry_sync_call()`'s 2–3 attempts safe — a retry after a partial failure must
  not move every level a second time.

**Ownership.** `user_topic_map` holds the rotation state (`last_cycle`, `times_tested`) and only
`user_topic_mapping` may write it, so **that module selects the topic slots** and hands them to
`on_topic_change(user_id, cycle_version, slots)`. Conversely `question_bank` belongs to
`user_test_mapping`, so the topic catalogue is read through
`UserTestMappingService.list_topics()`. The dependency stays one-directional:
`user_topic_mapping → user_test_mapping`, which is why `TopicSlot` lives in the latter's
`schemas.py`.

**`cycle_version` is owned here.** Born at 1 on signup; incremented on every row of the candidate
in `update_from_evaluation` after the ladder is applied, before the next test is assembled.

**Topics are reconciled on signup *and* on every evaluation**, so topics added to `question_bank`
after a candidate signed up start being rotated in rather than staying invisible to them.

## Data model

- **`question_bank`** (`user_test_mapping`, for now — see below) — a real, already-populated
  table (1,310 rows), not a designed one. See "Question bank: real data now reconciled" below
  for how that was discovered and resolved, and its actual columns.
- **`user_topic_map`** (`user_topic_mapping`) — `(user_id, topic)` PK, plus `section`,
  `current_level`, `pending_dir`, `last_cycle`, `times_tested` (all added by migration `0003`),
  `cycle_version`, `mastery_score`, `streak`, `updated_at`. Current state only: no history is
  stored, because every level move is reconstructible by joining `user_test_questions` and
  `evaluation_result` on `cycle_version`. `mastery_score` and `streak` survive as display-only
  fields and never feed the ladder — nothing reads the *column* back to move a level, though the
  scale behind it does drive DI (see "The level ladder"). `mastery_score` reads 0 for a question
  that was wrong, skipped or unreached.
- **`user_test_questions`** (`user_test_mapping`) — unique `(user_id, cycle_version)`,
  `sections` (JSON). A **resolved id list**, not a selection recipe: one key per section holding
  `budget_seconds` and a `questions` array of `{question_id, topic, level,
  expected_time_seconds, order, selection_fallback}`, with DI additionally carrying `set_id`,
  `topic`, `level` and `selection_fallback` at the section level. Question *content* is never
  denormalized here and **answer keys are never copied anywhere** — `/v1/tests/start` reads
  content back from `question_bank`.
- **`user_answers`** (`evaluation_report`) — unique `(user_id, cycle_version, question_id)`,
  `picked` (CHAR(1), nullable), `elapsed_seconds`, `unreached`.
- **`evaluation_result`** (`evaluation_report`) — unique `(user_id, cycle_version, question_id)`,
  `section`, `quadrant`. Reconcile rebuilds a dead background task's ladder input from these
  rather than re-scoring the paper.
- **`user_reports`** (`evaluation_report`) — unique `(user_id, cycle_version)`, `headline`,
  `tiles` (JSONB), `section_table` (JSONB), `findings` (JSONB), `actions` (JSONB), `questions`
  (JSONB, added by migration `0004` — the per-question review). Stored **fully rendered** rather
  than re-derived at read time: a report is a record of what the candidate was actually shown, so
  correcting an explanation in `question_bank` or retiring a question must not silently rewrite a
  report they already read. All three tables are written `ON CONFLICT DO NOTHING` — the first
  submission is the one that counts.

**Where `question_bank` lives right now:** it's defined in
`app/modules/user_test_mapping/models.py`, not in `question_generation`, because
`question_generation` is explicitly out of scope for this pass (directory + README only — see
"Phase 2"). Ownership moves to `question_generation` when that module is designed, following
apex-assessment's convention where the generating module owns the bank and every other module
reads it only through that module's `service.py`.

## Question bank: real data now reconciled

While scaffolding this service, a `.env` with a real `DATABASE_URL` and a
`scripts/verify_neon.py` (checking a `daily20_questions` table) turned up in this repo — neither
written by this scaffolding pass. Running `alembic upgrade head` against that real database
confirmed `daily20_questions` already existed with 1,310 curated rows across
`quant`/`reasoning`/`english`/`di`, and its schema didn't match the `question_bank` this pass
had designed from `daily20_prototype.html` alone (a guess made before this table's existence
was known).

**Resolved:** migration `0002` (`app/infrastructure/db/migrations/versions/
0002_rename_daily20_to_question_bank.py`) dropped the empty placeholder `question_bank` `0001`
had created and renamed `daily20_questions` into that name — no data copied or altered, just
the relation renamed. `app/modules/user_test_mapping/models.py`'s `QuestionBank` model was
rewritten to match the table's real columns:

`id` (text PK) · `section` · `topic` · `concept` (nullable) · `prerequisite_concept` (nullable)
· `method_tag` (nullable) · `question_text` · `option_a`-`option_d` (nullable) · `answer`
(CHAR(1), nullable, DB-enforced `CHECK ... IN ('A','B','C','D')`) · `explanation` (nullable) ·
`distractor_rationale_a`-`_d` (nullable) · `shortcut_available` (bool, nullable) ·
`shortcut_name` / `shortcut_how` (nullable) · `shortcut_saves_seconds` (nullable) ·
`difficulty` (smallint, nullable) · `expected_time_seconds` (nullable) · `source` (nullable) ·
`calibration` (nullable) · `batch_number` (nullable) · `set_id` (nullable) · `chart_type` /
`chart_image` / `chart_image_svg` / `chart_direction` (nullable) · `chart_data` (JSONB,
nullable). No `created_at` — the real table never had one.

**Local seed copy + manual-only seed script**, mirroring apex-assessment's
`export_question_bank.py` / `seed_reference_data.py` pattern but deliberately **not** wired
into app startup (the user asked for manual-only here, diverging from apex's
"seed-if-empty-on-boot" convention):
- `scripts/export_question_bank.py` — dumps the live `question_bank` table to
  `data/seed/question_bank.json` (committed; 1,310 rows, ~14MB). Re-run it any time the live
  table changes and the local fixture should catch up.
- `app/workers/seed_question_bank.py` — loads that fixture into a target DB,
  `ON CONFLICT (id) DO NOTHING` (safe to re-run). **Only invoked manually** —
  `./scripts/run_worker.sh seed_question_bank`, or inside a running container via
  `docker exec -it <container> uv run python -m app.workers.seed_question_bank`. Not called
  from `scripts/docker-entrypoint.sh`, `app.main`'s lifespan, or `scripts/run_api.sh` — those
  only ever run `alembic upgrade head`. Confirmed idempotent against the real Neon DB: a run
  against the already-seeded table reported `0` inserted, `1310` already present.

**Still open, not addressed here:** `scripts/verify_neon.py` has a live Neon password
hardcoded in plaintext (`CONN_STR = "postgresql://neondb_owner:...`). **Before running `git
init` in this directory, move that connection string into `.env` (already gitignored) and
reference it via `os.environ` instead** — otherwise the first commit puts a live credential in
history. Rotate it if it's ever been pushed anywhere already.

## Integration surface

REST (Node → this service), matching apex's `/v1` shape. No inbound auth — matches the rest of
the stack (Nest sends only `Content-Type` to apex and debug-assessment today; isolation is by
VPC/compose network).

| Endpoint | Body | Returns | Module |
|---|---|---|---|
| `POST /v1/users/signup` | `{user_id}` | `UserTopicMapOut` | `user_topic_mapping` |
| `POST /v1/tests/start` | `{user_id}` | `UserTestMapOut` | `user_test_mapping` |
| `POST /v1/tests/complete` | `TestCompletedIn` | **`ReportOut`** | `evaluation_report` |
| `GET /v1/reports/{user_id}` | `?cycle_version=N` (optional) | `ReportOut` | `evaluation_report` |
| `POST /v1/admin/reconcile` | `{user_id?}` | `ReconcileOut` | `evaluation_report` |
| `GET /v1/health`, `/v1/ready` | — | — | — |

`POST /v1/tests/start` returns full question content inline (prompt text, options, direction,
chart) — Nest has no direct access to this service's DB, matching apex's rule for the Backend
generally. It takes a **JSON body**, not a query param, matching the snake_case DTO contract
below. A candidate with no signup on record gets **404** (via `NotFoundError`, handled in
`app/main.py`), not a 500 — same for a report that doesn't exist.

**`POST /v1/tests/complete` returns the finished report**, the same `ReportOut` shape
`GET /v1/reports/{user_id}` serves, so Nest renders one component either way and the candidate
never makes a second call to find out how they did. The scaffold's `EvaluationOut` is deleted.
Resubmitting the same cycle returns the report already produced rather than re-scoring.

**`POST /v1/admin/reconcile`** re-runs post-evaluation work that never completed — see
"The evaluation pipeline" below. No inbound auth, like everything else here; it is safe to cron.

**DI charts ride on the section, not the question.** `SectionQuestions` carries `direction` and
`chart_svg` once; `QuestionOut.chart` is always null. A DI set's inline SVG is ~37KB and all
five questions share it, so repeating it per question would add ~150KB of duplicate payload to
every response. A full paper is ~51KB as it stands.

SQS: `subscriber.consume()` is wired for `algoaptitude-user-signup.fifo`, routing to the same
`handle_user_signup` the REST route calls (`app/modules/user_topic_mapping/handlers.py`). The
architecture diagram places the signup queue beside Node without settling whether Node relays
signup over REST, over SQS, or both — wiring both costs nothing here and defers that choice.
`evaluation-completed` **is now published**, from `evaluation_report`'s post-evaluation
background task — the first caller of `publisher.publish()` in the service. Nothing consumes it
yet. Publishing is best-effort and wrapped in a `try`: a queue failure must not cost the
candidate their already-applied ladder move. If `SQS_EVALUATION_COMPLETED_URL` is unset the
publish is skipped silently, which is the normal local-dev case.

**Local ElasticMQ:** the shared `infra/elasticmq.conf` must declare
`algoaptitude-user-signup.fifo` and `algoaptitude-evaluation-completed.fifo`
(`fifo = true, contentBasedDeduplication = false`) — the shared ElasticMQ container won't serve
an undeclared queue.

## The evaluation pipeline (`evaluation_report`)

**Built.** Shaped around one constraint: the candidate should see their report the moment they
finish. So `POST /v1/tests/complete` does only what the report needs, and everything the *next*
paper needs is backgrounded.

```
request path                                          background (own session, retry 2-3)
─────────────────────────────────────────             ──────────────────────────────────
1 query   already scored? -> return that
1 query   get_assembled_test                          update_from_evaluation
1 query   get_scoring_metadata      ─── spawn ───▶      ladder -> cycle+1
pure      classify x20 -> the report                    on_topic_change (tomorrow's paper)
3 inserts answers, results, report                      publish evaluation-completed
RETURN    ReportOut
```

**Measured against the real Neon DB, warm pool:** `GET /v1/health` (no DB) **0.8 ms** ·
`GET /v1/reports/{id}` (1 query) ~480 ms · `POST /v1/tests/complete` (6 queries) ~1.4 s · a bare
`SELECT 1` round-trip **98 ms**. The application contributes about a millisecond; the rest is
network latency to Neon, so **query count is the only lever that matters**. The scaffold's
ordering would have added roughly twenty more round-trips. If it ever needs to be faster the
levers are, in order of size: move the database closer, reconsider `pool_pre_ping=True` (one
extra round-trip per checkout), and fold the replay-check query into `save_report`'s
`ON CONFLICT ... RETURNING`. None are code problems in this module.

**The trade, stated plainly:** a background task that dies leaves the candidate scored but not
advanced, and `/v1/tests/start` would keep serving the paper they already sat.
`POST /v1/admin/reconcile` is the recovery — `{"user_id": ...}` for one, `{}` to sweep. Stuck
means *scored for cycle N while the topic map is still on cycle N*; each module queries its own
table for that (`latest_evaluated_cycles()` and `list_current_cycles()`), joined in Python.
Recovery rebuilds the ladder payload from `evaluation_result` plus the assembled test (for DI's
set→topic grouping) and calls the same `update_from_evaluation`, so the ladder's replay guard
makes a double-run a no-op.

**`created_at` is stamped in Python**, not left to the column default, so the report returned by
`/v1/tests/complete` is byte-identical to a later `GET /v1/reports/{user_id}`.

## Nest-side contract (documented here, implemented in a later pass — nothing in this repo)

- Env var `APTITUDE_API_URL=http://localhost:8090/v1` — follow the `ALGOAPEX_API_URL` form
  (`algojob_nest/src/config/configuration.ts`), not the legacy `PYTHON_*_SERVICE` one.
- Add an `httpClient.services.aptitude` profile copying `algoapex`'s (300s timeout, 2 retries,
  circuit breaker off, dedup off — AI-adjacent/slow services get long timeouts and no breaker
  in this codebase's convention).
- `src/aptitude/` mirroring `src/algoapex/`: a REST client going through Nest's
  `HttpClientService` (never raw axios), `@Controller('api/aptitude')` under
  `CandidateAuthGuard`, snake_case DTO fields matching this service's Pydantic schemas,
  `{ success, data }` response envelope.
- A `checkAptitude()` row in `src/health/integration-connectivity.service.ts`.
- Apex keys on the **Candidate `_id`**, not the auth user id
  (`AlgoApexService.resolveCandidateId`) — use the same identifier here so the two services
  agree on what "`user_id`" means.

### ⚠️ Name collision to settle before Nest work

`aptitude` already exists in Nest as an in-process MCQ practice topic —
`src/assessments/schemas/assessment.schema.ts:45`, the allowed-topics list in
`src/assessments/services/practice-sessions.service.ts:34`, plus a global **"Aptitude XP"
leaderboard** aggregating `topic: 'aptitude'`
(`practice-sessions.service.ts:1076-1394`). Either this new service takes that arena over, or
one of the two gets a distinct name. Doesn't block anything in this repo; does block the Nest
module.

## Config and gotchas

`.env.example` documents all keys. These are worth calling out because they'll silently break
things if missed, not because they're obscure — the three DSN issues below were all found by
actually running `alembic upgrade head` against a real Neon connection string during
scaffolding, not anticipated in the abstract:

- **The connection string Neon's dashboard gives you is a plain libpq URL** —
  `postgresql://...?sslmode=require&channel_binding=require` — not one SQLAlchemy's asyncpg
  dialect accepts as-is. `to_asyncpg_url()` (`app/infrastructure/db/session.py`) fixes all
  three problems: rewrites the scheme to `postgresql+asyncpg://` (bare `postgresql://` makes
  SQLAlchemy pick the sync `psycopg2` dialect, which isn't installed, and
  `create_async_engine` fails with `ModuleNotFoundError: psycopg2`); drops `sslmode=` (asyncpg
  treats it as an unknown server setting — TLS is requested instead via
  `connect_args={"ssl": "require"}`); drops `channel_binding=` (a libpq/psycopg-only param
  asyncpg doesn't understand at all). The same helper is reused by
  `app/infrastructure/db/migrations/env.py` for Alembic's own engine.
- **Neon's pooled (`-pooler`) endpoint is PgBouncer in transaction mode**, which breaks
  asyncpg's server-side prepared-statement cache. `connect_args={"statement_cache_size": 0}`
  works around it at a small per-query cost; the direct (non-pooled) endpoint doesn't need
  the workaround but tolerates it fine. **Use the direct endpoint for `DATABASE_URL` when
  running Alembic**, to be safe. (The scaffold's own verification ran successfully against the
  *pooled* endpoint — see "Verification" below — so the workaround is confirmed to work; the
  direct-endpoint recommendation is still the safer default.)
- **ASH creates its own `api_keys` table lazily** on first `get_key_handler()` call — it is
  deliberately **not** in the Alembic migration (see `migrations/env.py`'s comment). Don't add
  it there if you touch that file later.
- **Alembic advisory-lock id** (`MIGRATION_LOCK_ID = 48213907` in `migrations/env.py`) is
  distinct from apex-assessment's own lock ids (`91347701`, `728491001`) — needed because a
  shared Postgres *instance* (even with separate databases) could otherwise see lock-id
  collisions across services' migration runs.
- **Write migrations by hand.** `alembic revision --autogenerate` is not usable in this repo:
  `0002` renamed the real `daily20_questions` into `question_bank`, and that table's physical
  indexes still carry their original `daily20_*` names which `QuestionBank` deliberately does
  not declare — so autogenerate wants to drop and recreate them on every run. `0003` was
  written by hand for this reason.

**Ladder tunables** (all in `app/core/config.py`, documented in `.env.example`):
`INITIAL_TOPIC_LEVEL=2` · `MIN_TOPIC_LEVEL=1` · `MAX_TOPIC_LEVEL=5` ·
`DI_PROMOTE_SCORE_THRESHOLD=85` · `DI_DEMOTE_SCORE_THRESHOLD=40` · `TIME_BUDGET_SLACK=1.15`.
Structural constants that are *not* per-environment (section list and order, questions and
topics per section, the quadrant→signal map, per-section default question times) live in
`app/core/constants.py`, which the scaffold had reserved for exactly this.

**Errors → HTTP.** `app/core/exceptions.py` gained `NotFoundError(ModuleError)`, handled in
`app/main.py` so an unknown candidate on `POST /v1/tests/start` returns **404**, not a 500.
Other `ModuleError` subclasses still surface as 500s, which is right — they mean the bank is
unseeded or a cycle is out of order, not that the client asked for something absent.

**`scripts/verify_neon.py` is excluded from ruff** in `pyproject.toml`. CLAUDE.md previously
claimed this was already the case; it wasn't, so `ruff check .` had 8 pre-existing failures.
The exclusion does not make its hardcoded credential (below) any less of a problem.

## Keep ASH

`api-service-handler` (the encrypted API-key pool apex uses) is fully wired up —
`app/infrastructure/keys/handler.py`, `ASH_SHARED_SECRET`, the `keys add/bulk-add/list/delete`
CLI (`./scripts/run_worker.sh keys ...`), `close_key_handler()` in the app lifespan's teardown
— even though nothing in this pass consumes a key. It has no LLM SDK dependency of its own, so
standing it up now costs nothing and means Phase 2 question generation (which will need
OpenAI/Anthropic/etc. keys) doesn't have to retrofit secrets handling later.

## Stack registration

**Done** (low-risk, additive — nothing else depends on these lines yet):
- `../dev.sh` — a commented `SERVICES` entry (`aptitude | aptitude-assessment | ./run.sh`),
  matching the other native-dev services.
- `../infra/elasticmq.conf` — declares `algoaptitude-user-signup.fifo` and
  `algoaptitude-evaluation-completed.fifo`.
- `../RUNBOOK.md` — a service section (native `./run.sh` path only) + the port table (8090) +
  the top health-check block.

**Specified here, not applied** — the root `docker-compose.yml` / root `Dockerfile` bake
`apex-assessment`, `debug-assessment`, `interview_manager` and `algojob-agent-server` into one
shared multi-stage image (`algojob-stack:latest`), each service getting its own `working_dir`
inside that image. Adding `aptitude-assessment` there means editing the *build stages* of a
Dockerfile that four other live services currently build from — a materially bigger, riskier
change than scaffolding this repo, so it wasn't made in this pass. What it needs, precisely:

- Root `Dockerfile`: in the `py-build` stage, add
  `COPY aptitude-assessment/pyproject.toml aptitude-assessment/uv.lock aptitude/` +
  `RUN cd aptitude && uv sync --frozen --no-install-project --no-dev`, then later
  `COPY aptitude-assessment/ aptitude/` + `RUN cd aptitude && uv sync --frozen --no-dev`
  (mirroring the existing `apex`/`personalized` blocks). In the final stage, add
  `COPY --from=py-build /app/aptitude /app/aptitude`. Update the port-list comment near the
  bottom of the file.
- Root `docker-compose.yml`: a new block under the `x-app: &app` anchor —
  ```yaml
  aptitude:
    <<: *app
    profiles: ['aptitude']
    container_name: algojob-aptitude
    working_dir: /app/aptitude
    command: /app/aptitude/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090
    env_file: ./aptitude-assessment/.env
    environment:
      LOG_LEVEL: INFO
      RUN_CONSUMERS: 'true'
      SQS_ENDPOINT_URL: http://elasticmq:9324
      SQS_USER_SIGNUP_URL: http://elasticmq:9324/000000000000/algoaptitude-user-signup.fifo
      SQS_EVALUATION_COMPLETED_URL: http://elasticmq:9324/000000000000/algoaptitude-evaluation-completed.fifo
    depends_on:
      elasticmq:
        condition: service_healthy
    ports:
      - '8090:8090'
  ```
  Also add `aptitude` to `nest`'s service block: `ALGOAPTITUDE_API_URL: http://aptitude:8090/v1`.
- Root `README.md`'s Docker-stack sections (service lists, `--only=`, single-service table) —
  cosmetic, but only accurate once the two edits above land.

## Verification

What was actually run and confirmed, against the real `.env`/Neon setup that turned up during
scaffolding (see "Question bank: real data now reconciled" above):

```
uv sync                    # resolves cleanly, all deps install
uv run ruff check .        # clean (scripts/verify_neon.py excluded via pyproject.toml)
uv run mypy app            # clean, strict mode, 63 files
uv run pytest tests/unit   # 140 passed
uv run alembic upgrade head   # 0001 -> 0004, all succeeded against the real Neon
uvicorn app.main:app       # boots; GET /v1/health -> 200 with all 4 module versions;
                            #   GET /docs -> 200
uv run python scripts/export_question_bank.py       # wrote data/seed/question_bank.json,
                                                       #   1,310 rows
uv run python -m app.workers.seed_question_bank      # 0 inserted, 1310 already present
                                                       #   (idempotency confirmed)
```

**140 unit tests** cover the pure logic exhaustively rather than by sampling —
`test_ladder.py` / `test_rotation.py` / `test_selection.py` for the two mapping modules, and
`test_scoring.py` / `test_report.py` (76 of them) for the evaluation model: every
`(pending_dir, signal)` transition, both level clamps, every quadrant boundary from both sides
(`elapsed == expected`, `elapsed == 0.5 × expected`, skip vs. unreached, a null answer key), the
DI score against all three bands, round-robin ordering and tie-breaking, all five
selection-fallback steps, and each finding threshold plus the action list's priority, dedupe and
cap.

**End-to-end against the real Neon DB**, driving all three modules and cleaning up after itself.
The mapping half — all confirmed:

- signup seeds **54 topic rows** (`di 3, english 17, quant 17, reasoning 17`), all at level 2,
  cycle 1
- a duplicate signup changes nothing **and does not advance the rotation** (the invariant above)
- `/v1/tests/start` returns 4 sections / 20 questions, DI's five all from one `set_id`, chart
  carried once on the section; budgets `di 478 · quant 286 · reasoning 278 · english 219`
  (~21 min at level 2, vs. the prototype's fixed 25:00)
- **no `answer`, `explanation`, `shortcut_*` or `distractor_rationale_*` key appears anywhere in
  the response body**
- `/start` is byte-identical on a second call
- one `mastered` opens a probation without moving the level; a second promotes; a `gap` between
  two cancels
- an evaluation replayed at a stale `cycle_version` is a no-op
- across 7 cycles: 140 questions served with **zero repeats**, all 17 quant topics tested,
  levels above 2 reached in both DI and non-DI sections
- an unknown candidate raises `NotFoundError` → HTTP 404

The evaluation half — all confirmed, over both the service API and real HTTP:

- a perfect paper returns a full `ReportOut`: 4 section rows, 20 question reviews, tiles summing
  to 20, the "Strong run" headline, every section 5/5
- the `unreached` tile is hidden when it did not happen, so the four shown tiles add up
- **mastered questions carry no shortcut** — telling someone who nailed it about a faster route
  is noise
- **the string "over-sample" appears nowhere** in the response (the prototype's dropped promise)
- resubmitting returns a byte-identical report and does not double-write answers
- the background task advances the cycle, opens the right probations, and assembles a new paper
  with zero repeated questions
- a crafted paper (2 fragile, 2 careless under budget, gaps sharing prerequisites) produces the
  "too slow", "rushed with time in hand" and "clean" findings, tones spanning good→serious,
  6 actions capped and gap-first
- `GET /v1/reports/{id}` returns the stored report; `?cycle_version=1` still returns cycle 1's,
  per-question review intact
- **reconcile**: a simulated dead task leaves the candidate stuck on cycle 3; `POST
  /v1/admin/reconcile {}` reports `scanned=1 reconciled=1`, advances the cycle, assembles the
  next paper; running it again reports 0
- a fourth cycle brings topics back around and promotes them, clearing their probation
- `GET /v1/reports/{id}?cycle_version=99` → **404**, not 500

All 6 tables live in Neon: `user_topic_map`, `question_bank` (the renamed, real
`daily20_questions` — 1,310 rows), `user_test_questions`, `user_answers`,
`evaluation_result`, `user_reports`. `daily20_questions` no longer exists as a separate table —
it *is* `question_bank` now.

## Phase 2 (question generation, ~2 months out)

Not built. `app/modules/question_generation/` has only `__init__.py` (with `__version__`) and
a `README.md` pointing back here. Until it lands:
- Questions are curated externally and imported via
  `app/workers/import_question_bank.py --path <path>` — currently a CLI argument-parser stub
  that raises `NotImplementedError`. The path will be shared later.
- `question_bank`'s real owner will be `question_generation`, not `user_test_mapping` (see
  "Data model" above).
- No LLM provider SDKs (`openai`, `anthropic`, etc.) are dependencies yet — add them alongside
  this module, and wire `app/infrastructure/keys/handler.py:get_key_value()` to fetch from ASH.

## What's left

All three pipeline modules are **done**. The remaining work is outside them:

1. **The Nest module** (`src/aptitude/`) — nothing in this repo. See "Nest-side contract" above,
   and settle the `aptitude` name collision first.
2. **Docker/compose registration** — see "Stack registration"; specified but not applied, because
   it means editing build stages four live services share.
3. **`question_generation`** — Phase 2, above.
4. **`scripts/verify_neon.py`'s hardcoded credential** — still unaddressed, and still blocking a
   clean `git init` here.

This file gets a section updated in place per module as each is built — the same convention
`apex-assessment/CLAUDE.md` follows. Module `__version__` strings are still `0.1.0` everywhere
and should move now that three of the four are real.

### Open questions

- **Topic granularity, especially DI.** Three DI topics (Bar/Pie/Table Charts) is coarse, and
  because DI sets are fixed 1→4 ramps its level ladder is currently inert. Re-granularizing DI
  by the existing `concept` column into ~15 topics is the obvious next move, and the schema is
  shaped so it needs no change to the ladder, rotation or scoring logic — only data curation.
- **Level 1 and 5 are barely stocked.** Minimum per-topic supply is 0 at both; the median topic
  has one L1 question and zero or one L5. The fallback ladder absorbs it, but the bank wants
  filling out at the extremes before the ladder's full range means much.
- **Nothing is calibrated.** Every row is `source='authored'`, `calibration='model_estimate'`,
  so `difficulty` (the level) and `expected_time_seconds` are model guesses treated as ground
  truth — and `expected_time_seconds` now decides three things: mastered vs. fragile, careless
  vs. gap, and the section budget. This is the single biggest lever on report quality.
- **`shortcut_available` is only 30% of the bank**, 13% in English. The code handles it — no
  action is emitted and the "each of these has a shortcut" line is suppressed — but the
  `fragile` verdict is much less useful in English than the prototype's demo data suggests.
- **The reconcile sweep is unscheduled.** `POST /v1/admin/reconcile` exists and is safe to cron;
  nothing calls it yet. Nest or an external scheduler should, otherwise a candidate stranded by a
  dead background task stays stranded until someone notices.
- **Per-section wall-clock is optional and currently unsent.** `TestCompletedIn.sections` lets
  Node report true time-per-section; without it the service sums per-question `elapsed_seconds`,
  which slightly under-counts and makes the "rushed with time in hand" finding conservative.
- **Report derivation.** `UserReport` persists everything fully rendered, deliberately — a report
  is a record of what the candidate saw. If storage ever becomes a concern, the per-question
  review is by far the largest field and the most re-derivable.
