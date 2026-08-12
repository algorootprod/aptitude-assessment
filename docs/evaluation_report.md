# `evaluation_report`

Scores a completed paper, produces the diagnostic report, and feeds the level ladder. It owns
`user_answers`, `evaluation_result` and `user_reports`.

The report is the product. Not the score — the service deliberately never shows one — but the
answer to *what kind of mistake was that*: a knowledge gap, carelessness, or a method that works
but is too slow.

```
app/modules/evaluation_report/
  models.py       UserAnswer, EvaluationResult, UserReport
  schemas.py      TestCompletedIn, ReportOut, ReportQuestionReview, ReconcileIn/Out
  scoring.py      classify, di_section_score        pure — the quadrant rule
  report.py       build_headline/tiles/...          pure — the prototype's report(), ported
  repository.py   all SQL for the three tables
  service.py      evaluate, get_report, reconcile
```

`scoring.py` and `report.py` take no session and no ORM objects, so every rule that decides what
a candidate is told is unit-testable without a database — `tests/unit/test_scoring.py` and
`test_report.py`, 76 tests between them.

---

## The pipeline is inverted from the scaffold's

The scaffold specified: score, then **synchronously** run the level ladder and assemble
tomorrow's paper, then build the report in the background. That makes the candidate wait for
~20 Neon round-trips of work they do not need, and then wait again for the thing they actually
came for.

The stated constraint for this module was speed — minimum friction between finishing the test
and seeing the report — so the ordering flipped:

```
POST /v1/tests/complete
  1 query    get_report            already scored? then return that, unchanged
  1 query    get_assembled_test    section budgets, order, DI set membership
  1 query    get_scoring_metadata  answer keys + coaching material
  pure       classify x20  ->  headline, tiles, sections, findings, actions, reviews
  3 inserts  answers, results, report      bulk, all ON CONFLICT DO NOTHING
  spawn      background task
  RETURN     the full report

background (own session, retried 2-3x):
  ladder -> cycle+1 -> assemble the next paper -> publish evaluation-completed
```

Nothing in the background half is needed until the candidate next sits down.

### What that actually costs

Measured against the real Neon database, warm pool:

| | |
|---|---|
| `GET /v1/health` (no DB) | **0.8 ms** |
| `GET /v1/reports/{id}` (1 query) | ~480 ms |
| `POST /v1/tests/complete` (6 queries) | ~1.4 s |
| a bare `SELECT 1` round-trip | **98 ms** |

The application contributes about a millisecond. Everything else is network round-trip to Neon,
so **query count is the only lever that matters** — which is exactly what this design minimises.
The old ordering would have added roughly twenty more round-trips, about two seconds.

If this ever needs to be faster, the levers in order of size are: move the database closer,
reconsider `pool_pre_ping=True` (it costs one extra round-trip per checkout), and fold the
replay-check query into `save_report`'s `ON CONFLICT ... RETURNING`. None of them are code
problems in this module.

---

## The quadrant rule — `scoring.py`

Ported from the prototype's `quadOf()`:

| Quadrant | Rule | What it means |
|---|---|---|
| `mastered` | correct, `elapsed <= expected` | clean |
| `fragile` | correct, `elapsed > expected` | knows it, too slow |
| `careless` | wrong, `elapsed < 0.5 x expected` | rushed — not a knowledge gap |
| `gap` | wrong, `elapsed >= 0.5 x expected` | the method isn't there yet |
| `unreached` | the section clock expired first | — |

Two behaviours worth knowing before you change anything:

- **A skip is `careless`, not `gap`.** Leaving a question blank with time still on the clock is
  the same failure as answering too fast. Only a genuinely unreached question is `unreached`.
- **A missing answer key can never score correct.** `question_bank.answer` is nullable, and an
  unscoreable question must not read as mastered.

### The DI score

`user_topic_mapping`'s ladder needs a 0–100 number for DI, because a DI section is one whole
`set_id` — five questions sharing a chart and one topic — so its signal cannot come from a single
question's quadrant. **This module is the only place that number is produced**; the ladder only
bands it at 85/40, and the prototype has no scoring function to port because it deliberately
never shows a score.

`di_section_score` averages `QUADRANT_MASTERY_SCORE` — mastered 100, fragile 70, careless 30,
gap 0, unreached 0 — the same mapping `user_topic_mapping` already uses for its display field.
That keeps speed in the signal:

| Set result | Score | Ladder |
|---|---|---|
| 5 mastered | 100 | promote |
| 4 mastered + 1 fragile | 94 | promote |
| 4 mastered + 1 gap | 80 | hold |
| 5 fragile (all right, all slow) | 70 | hold |
| 2 mastered + 3 gap | 40 | hold (not strictly `< 40`) |
| 1 mastered + 4 gap | 20 | demote |

---

## The report — `report.py`

Six parts, in the order they are read.

**`headline`** — one sentence naming the shape of the run, first match wins: three or more
`fragile` → "You know more than the clock is letting you show." · three or more `careless` ·
five or more `gap` · fifteen or more `mastered` · else "A mixed run". The prototype carries the
comment *"describe the shape, never lead with a score"*, and that is a product decision, not a
stylistic one.

**`tiles`** — a count per quadrant. The four verdicts are always shown; `unreached` appears only
when it happened, so in the normal case the four tiles visibly sum to 20.

**`section_table`** — right/total, clock used against budget, and one sentence chosen by a
ladder where the first true thing wins: questions never reached › ran to the limit (>92% of
budget) › rushed with time in hand › finished but over budget › comfortable.

**`findings`** — the patterns. Two or more `fragile` in a section; two or more `careless` in a
section *while under 75% of its budget*; the same `prerequisite_concept` behind two or more
gaps anywhere in the paper; and a roll-up of everything clean. One slow answer is not a pattern —
that threshold is the whole idea. If nothing fires, the report says "Nothing stands out" rather
than inventing something.

**`actions`** — a prescription per weak point: `gap` → "Relearn {prerequisite_concept}" ·
`fragile` → that question's own shortcut · `careless` → pacing advice. Sorted by that priority,
deduped by heading, capped at six.

**`questions`** — every question with the worked explanation, the rationale for *the option the
candidate actually picked*, and the shortcut — the last only when they did not already answer it
cleanly, since telling someone who nailed it about a faster route is noise.

### Two places the data pushes back

- **`shortcut_available` is true for only 30% of the bank** (13% in English). A `fragile` answer
  with no shortcut on file produces no action rather than an empty one, and the "each of these
  has a shortcut you are not using" line is only printed when it is actually true.
- **`prerequisite_concept`, `explanation` and the `distractor_rationale_*` columns are all
  nullable.** A gap with no prerequisite falls back to its topic, so every gap still gets
  prescribed something rather than "Relearn None".

### One deliberate departure from the prototype

The prototype's actions block closes with *"Tomorrow's set will over-sample your weaker topics."*
That is not what happens — topic rotation is strict round-robin and ignores performance entirely;
only a topic's **level** moves. The line is dropped rather than shipped as a promise the service
does not keep.

---

## Storage

Reports are stored **fully rendered**, including the per-question review (`user_reports.questions`,
added by migration `0004`). A report is a record of what the candidate was actually shown:
correcting an explanation in `question_bank`, or retiring a question, must not silently rewrite
a report they already read.

`save_answers`, `save_results` and `save_report` are all `ON CONFLICT DO NOTHING`. The first
submission is the one that counts — a resubmitted paper cannot overwrite the original timings the
report rests on, and `evaluate()` short-circuits to the stored report anyway.

`created_at` is stamped in Python rather than left to the column default, so the report returned
by `POST /v1/tests/complete` is byte-identical to a later `GET /v1/reports/{user_id}`.

---

## Background work and `POST /v1/admin/reconcile`

`app/core/tasks.py:spawn` keeps a strong reference to each task (asyncio holds only weak ones, so
a bare `create_task` can be garbage-collected mid-flight) and logs anything it raises. The app's
shutdown calls `drain()` **unconditionally** — it previously sat inside `if tasks:`, so the grace
period explicitly labelled "for in-flight fire-and-forget report-storing tasks" only ran when SQS
consumers happened to be enabled.

The task opens **its own session per retry attempt**: the request session is committed and closed
before it runs, and a failed transaction poisons its session, so retrying on the same one would
fail identically.

If the task dies anyway — a restart mid-flight, a DB blip outlasting its retries — the candidate
is left scored but not advanced, and `/v1/tests/start` would keep serving the paper they already
sat. That is what reconcile is for:

```
POST /v1/admin/reconcile  {"user_id": "..."}   fix one
POST /v1/admin/reconcile  {}                   sweep every stuck candidate

200 {"scanned": 214, "reconciled": [{"user_id": "...", "cycle_version": 4,
                                     "action": "ladder_applied"}], "skipped": 213}
```

**Stuck** = scored for cycle N while the topic map is still on cycle N. Finding those crosses a
table boundary, so each module queries its own: `latest_evaluated_cycles()` here,
`list_current_cycles()` on `user_topic_mapping`, joined in Python.

Recovery rebuilds the ladder payload from the persisted `evaluation_result` rows plus the
assembled test (for DI's set→topic grouping) and calls the same `update_from_evaluation`.
Idempotent: a candidate who is not stuck is skipped, and the ladder's own replay guard would
reject a duplicate regardless. Safe to run on a schedule.

---

## Things to know before changing this

- **`EvaluationOut` no longer exists.** `/v1/tests/complete` returns `ReportOut` — the same shape
  `GET /v1/reports/{user_id}` returns, so Nest renders one component either way.
- **The only route to an answer key** is `UserTestMappingService.get_scoring_metadata`. This
  module never reads `question_bank` directly, and `/v1/tests/start` still strips every field
  this one needs.
- **Nothing consumes `evaluation-completed` yet.** Publishing is best-effort and wrapped: a queue
  failure must not cost the candidate their already-applied ladder move.
- **Nothing is calibrated.** Every bank row is `calibration='model_estimate'`, so
  `expected_time_seconds` — which decides mastered vs. fragile, careless vs. gap, and the section
  budget — is a model guess this module treats as ground truth.
- Adding a column? Write the migration **by hand**; autogenerate is unusable in this repo (see
  CLAUDE.md, "Config and gotchas").
