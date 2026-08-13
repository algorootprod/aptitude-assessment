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

Ported from the prototype's `quadOf()`. `classify()` compares `elapsed_seconds` against two
thresholds derived from the question's own `expected_time_seconds` (`E`), both named constants
in `app/core/constants.py`:

```
mastered_limit  = E × EXPECTED_TIME_MASTERED_FACTOR   = E × 1.0
careless_limit  = E × CARELESS_TIME_FRACTION          = E × 0.5
```

| Quadrant | Rule | What it means |
|---|---|---|
| `mastered` | correct, `elapsed <= mastered_limit` (`elapsed <= E`) | clean |
| `fragile` | correct, `elapsed > mastered_limit` (`elapsed > E`) | knows it, too slow |
| `careless` | wrong, `elapsed < careless_limit` (`elapsed < 0.5E`), **or skipped with time left** | rushed — not a knowledge gap |
| `gap` | wrong, `elapsed >= careless_limit` (`elapsed >= 0.5E`) | the method isn't there yet |
| `unreached` | `unreached` flag set — the section clock expired first | — |

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

`di_section_score` averages `QUADRANT_MASTERY_SCORE` — **mastered 100, fragile 50, careless 0,
gap 0, unreached 0** — the same mapping `user_topic_mapping` already uses for its display field
and the report's per-section score card:

```
di_section_score = mean(QUADRANT_MASTERY_SCORE[q] for q in the set's 5 questions)
                  = (100 × #mastered + 50 × #fragile) / 5        (careless/gap/unreached add 0)
```

`user_topic_mapping`'s ladder then bands that score: `> DI_PROMOTE_SCORE_THRESHOLD (85) → +1`,
`< DI_DEMOTE_SCORE_THRESHOLD (40) → −1`, else `0`. That keeps speed in the signal:

| Set result | Score | Ladder |
|---|---|---|
| 5 mastered | 100 | promote |
| 4 mastered + 1 fragile | 90 | promote |
| 3 mastered + 2 fragile | 80 | hold |
| 4 mastered + 1 gap | 80 | hold |
| 5 fragile (all right, all slow) | 50 | hold |
| 2 mastered + 3 gap | 40 | hold (not strictly `< 40`) |
| 1 mastered + 4 gap | 20 | demote |
| 1 mastered + 4 careless | 20 | demote |

> `careless` and `gap` score identically (both 0) on this scale, so a DI section cannot signal
> "rushed" apart from "doesn't know it" — see the constant's own docstring in `constants.py` for
> why that's accepted for now.

### The section progress score

The 0–100 figure a candidate is *shown* per section — the score card in `dev-ui`, the y-axis of
the progress chart, `UserTopicMapOut.section_progress`, `GET /v1/progress/{user_id}` — is not
produced by this module, but half its input is. The split is worth knowing before changing either
side:

| Term | What it is | Where it comes from |
|---|---|---|
| `s` | the raw 0–100 for that section's last sitting | **this module** — `QUADRANT_MASTERY_SCORE`, averaged |
| `L` | the section's level, 1–5 | `user_topic_mapping`'s ladder |
| `O` | the two combined | `app/modules/user_topic_mapping/progress.py` |

```
O = (100 / Λ) × [(L − 1) + s / 100]        Λ = MAX_TOPIC_LEVEL = 5

L1  0–20    L2  20–40    L3  40–60    L4  60–80    L5  80–100
```

Each level owns an equal 20-point slice, so "scored full marks at this level" and "moved up a
level" are worth exactly the same. L5 with `s = 100` is 100; L1 with `s = 0` is 0. (Ported from
apex-assessment's `app/core/cefr.py:skill_progress_score`, where Λ = 6. Only the formula came
across — apex's `LEVEL_BANDS` is a *different*, non-uniform concept and must not be mixed in.)

**Where `s` comes from is this module's output.** Non-DI sections carry one question per topic, so
each topic's `mastery_score` is just `QUADRANT_MASTERY_SCORE[quadrant]` and the section's mean is
the same number `dev-ui` puts on the section score card (`dev-ui/lib/quadrants.js:scoreOf`, the
same table mirrored client-side). DI's five questions share one topic, so its `mastery_score` is
the `di_section_score` above. Either way it reaches `progress.py` through `user_topic_map`, not
through a read of `evaluation_result`.

**Which topics count.** `section_progress()` takes the cohort `last_cycle == cycle_version − 1` —
the topics the *last evaluated* test actually covered — then `L` = the most repeated level in that
cohort (lowest on a tie, so the figure never overstates) and `s` = their mean `mastery_score`. The
cohort is deliberately not `times_tested > 0`: that counter is incremented when a topic is
*scheduled*, so it would pull in topics queued for the in-flight test whose `mastery_score` is
still `0.0` and drag every section down. `tests/unit/test_progress.py` pins that trap.

Worked, for a quant section of 5 topics all at level 2 scoring 3 mastered + 1 fragile + 1 gap:

```
s = (100 + 100 + 100 + 50 + 0) / 5 = 70
O = (100 / 5) × [(2 − 1) + 0.70]   = 34.0        → level 2's band, 70% of the way through it
```

**Every section reads 0.0 until the first evaluation.** That is an explicit `cycle_version <= 1`
guard, not a derivation: at signup `cycle_version − 1 == 0` matches the never-scheduled topics and
would report a meaningless 20.0 off their seeded level 2. `SectionStanding.level` and `.raw` are
`None` there — not measured is not the same as scored zero.

Each point is appended to `user_section_progress` (unique `(user_id, section, cycle_version)`,
`ON CONFLICT DO NOTHING`) inside `update_from_evaluation`, *before* the next test is assembled and
from the same `section_progress()` call `get_for_user` reports — so the stored history and the
standing on screen are one computation rather than two that agree by luck. The `DO NOTHING` is
what keeps it safe under this module's `retry_sync_call()`: a retry must not append a second point
or rewrite one the candidate has already seen.

---

## The report — `report.py`

Six parts, in the order they are read. Every threshold below is a named constant in
`app/core/constants.py`.

**`headline`** (`build_headline`) — one sentence naming the shape of the run. Counts quadrants
across all 20 questions, first match wins:

```
count(fragile)  >= HEADLINE_FRAGILE_THRESHOLD  (3)  → "You know more than the clock is letting you show."
count(careless) >= HEADLINE_CARELESS_THRESHOLD (3)  → "You're losing marks to speed, not to knowledge."
count(gap)      >= HEADLINE_GAP_THRESHOLD      (5)  → "There are real gaps to close before speed becomes the problem."
count(mastered) >= HEADLINE_MASTERED_THRESHOLD (15) → "Strong run. The remaining issues are narrow and fixable."
else                                                → "A mixed run — three different things are going on."
```

The prototype carries the comment *"describe the shape, never lead with a score"*, and that is a
product decision, not a stylistic one.

**`tiles`** (`build_tiles`) — a count per quadrant, in `TILE_ORDER` (mastered, fragile, careless,
gap, unreached). The four real verdicts are always shown; `unreached` appears only when its count
is non-zero, so in the normal case the four visible tiles sum to 20.

**`section_table`** (`build_section_table` / `_section_note`) — right/total, clock used against
budget, and one sentence chosen by a ladder where the first true thing wins:

```
unreached > 0                                                 → "{n} questions never reached."
time_used / budget > SECTION_TIME_PRESSURE_FRACTION (0.92)    → "Ran right to the limit — no slack for a hard item."
count(careless) >= FINDING_CARELESS_THRESHOLD (2)              → "{spare}s left unused, and {n} answers lost to haste."
count(fragile)  >= FINDING_FRAGILE_THRESHOLD  (2)               → "Finished, but {n} answers came in over budget."
else                                                           → "Comfortable — {spare}s to spare."
```

**`findings`** (`build_findings`) — the patterns, not per-question noise. One slow answer is not
a pattern; that threshold is the whole idea:

- **Fragile cluster** — a section with `count(fragile) >= FINDING_FRAGILE_THRESHOLD (2)`.
- **Rushed with time in hand** — a section with `count(careless) >= FINDING_CARELESS_THRESHOLD
  (2)` **and** `time_used_seconds < budget_seconds × CARELESS_BUDGET_FRACTION (0.75)`.
- **Shared root cause** — the same `prerequisite_concept` behind
  `count >= FINDING_PREREQUISITE_THRESHOLD (2)` `gap` questions anywhere in the paper (sorted
  alphabetically by prerequisite).
- **Clean roll-up** — if any `mastered` questions exist, one finding naming up to
  `MASTERED_TOPICS_NAMED (4)` of their topics.
- If none of the above fire, the single finding is "Nothing stands out" rather than inventing a
  pattern.

**`actions`** (`build_actions`) — a prescription per weak point, sorted by priority
(`ACTION_PRIORITY_GAP=0` < `ACTION_PRIORITY_FRAGILE=1` < `ACTION_PRIORITY_CARELESS=2`), deduped
by heading, capped at `MAX_ACTIONS (6)`:

- every `gap` question → `"Relearn {prerequisite_concept or topic}"`, detail = the question's
  `explanation` (or a generated fallback sentence if null).
- every `fragile` question **that has a shortcut on file** (`shortcut_name` and `shortcut_how`
  both set) → heading = `shortcut_name`, detail = `shortcut_how`. A fragile question with no
  shortcut produces no action rather than an empty one.
- all `careless` questions together → **one** action, "Give each question its full budget", with
  pacing advice naming the count and the affected topics.

**`questions`** (`build_question_reviews`) — every question, in sat order, with the worked
explanation, the rationale for *the option the candidate actually picked*
(`distractor_rationale`), and the shortcut fields — the last three are `null`ed out when
`quadrant == "mastered"`, since telling someone who nailed it about a faster route is noise.

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
