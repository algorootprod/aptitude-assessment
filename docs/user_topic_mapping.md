# `user_topic_mapping`

Owns two things nothing else in the service may write: **each topic's level** for a candidate,
and the candidate's **`cycle_version`**. It is the entry point for both signup and evaluation, so
every change to a candidate's state passes through here.

**Table:** `user_topic_map` — sole writer.

```
app/modules/user_topic_mapping/
  models.py       UserTopicMastery              the row
  schemas.py      TopicOutcomeIn, EvaluationResultIn, UserTopicMapOut
  ladder.py       signal_for, apply_signal      pure — how a level moves
  rotation.py     select_slots                  pure — which topics get tested
  repository.py   all SQL for user_topic_map
  service.py      the three entry points
  handlers.py     SQS entrypoint -> handle_user_signup
```

`ladder.py` and `rotation.py` take no session and touch no ORM class (they accept
`Protocol`-typed stand-ins), so the rules that decide a candidate's level and paper are testable
without a database — see `tests/unit/test_ladder.py` and `test_rotation.py`.

---

## The row

One per `(user_id, topic)`. Current state only — no history, because every level move is
reconstructible by joining `user_test_questions` (what was asked, at which level) and
`evaluation_result` (how it went) on `cycle_version`.

| Column | Meaning |
|---|---|
| `user_id`, `topic` | composite PK |
| `section` | denormalized from `question_bank` so rotation can order per section without a join |
| `current_level` | 1–5. **Maps 1:1 onto `question_bank.difficulty`** — there is no separate level column anywhere |
| `pending_dir` | probation: `+1` promotion pending, `-1` demotion pending, `0` neutral |
| `last_cycle` | cycle this topic was last **scheduled** into a paper. `0` = never. Drives round-robin |
| `times_tested` | count of times scheduled |
| `cycle_version` | the candidate's cycle counter, same value on all their rows |
| `mastery_score` | last observed 0–100, **display only** — never feeds the ladder |
| `streak` | consecutive positive signals, **display only** |

A fresh candidate gets **54 rows**: `di 3`, `quant 17`, `reasoning 17`, `english 17`.

---

## `handle_user_signup(user_id)`

Called by `POST /v1/users/signup` **and** by the `algoaptitude-user-signup.fifo` consumer
(`handlers.py`). Both firing for the same candidate is the expected case, so every step is
idempotent.

1. **`_reconcile_topics`** — insert a row per `(section, topic)` from
   `UserTestMappingService.list_topics()` at `INITIAL_TOPIC_LEVEL` (2),
   `ON CONFLICT (user_id, topic) DO NOTHING`. The topic list is read through the *service* of the
   module that owns `question_bank`, never from its tables.
2. **`_assemble_next_test`** — see below.
3. Return the snapshot.

Raises `TopicMappingError` if the bank has no topics at all, with the fix in the message
(`./scripts/run_worker.sh seed_question_bank`) — an empty bank otherwise produces a candidate
with zero topics and a baffling failure two calls later.

---

## `_assemble_next_test(user_id, rows, cycle_version)`

```python
if await self.test_mapping.has_test_for_cycle(user_id, cycle_version):
    return                                    # ← this guard is load-bearing
slots = select_slots(list(rows))
await self.repo.mark_scheduled(user_id, [s.topic for s in slots], cycle_version)
await self.test_mapping.on_topic_change(user_id, cycle_version, slots)
```

> ### Why the guard exists
>
> Test *assembly* is idempotent on its own — `on_topic_change` returns an already-assembled cycle
> unchanged. **Rotation is not.** Without this check, a signup arriving over both REST and SQS
> runs `select_slots` twice: the first call marks topics 1–5 as scheduled, so the second call
> picks topics 6–10 and marks those too. The candidate's first paper shows topics 1–5 while
> topics 1–10 are recorded as served, and five topics are silently skipped forever.
>
> This was a real bug, caught by the end-to-end check noticing 17 quant topics marked tested
> after 3 cycles instead of 15. `tests` cover the ladder and rotation; this one only shows up
> against a database, so the e2e asserts it explicitly.

### Why topic selection lives here, not in `user_test_mapping`

Selecting topics reads and writes `last_cycle` and `times_tested`, which are `user_topic_map`
columns — and only this module may write that table. So this module picks the slots and passes
them over. That keeps the dependency one-directional (`user_topic_mapping → user_test_mapping`),
which is also why `TopicSlot` is defined in the *other* module's `schemas.py`: importing it here
would otherwise close the cycle.

---

## Rotation — `rotation.py`

**Strict round-robin, blind to probation.**

```python
in_section = sorted(rows_for_section, key=lambda r: (r.last_cycle, r.topic))
slots = in_section[:wanted]
```

No cursor column is needed: a never-scheduled topic sits at `last_cycle = 0` and sorts first. The
`topic` tie-break makes a replayed cycle produce the identical paper.

`wanted` comes from `TOPICS_PER_SECTION`: **5** for the 17-topic sections, **1 for DI** — a DI
section is one whole `set_id`, five questions sharing a chart and a topic.

Each slot carries that topic's *own* `current_level`, so after a few cycles one section's five
questions are generally **not** all the same level.

### The consequence you accepted

17 topics ÷ 5 slots means a topic comes back around every ~4 cycles, so **a probation opened in
cycle N resolves in cycle N+4** (N+3 for DI's 3 topics). Rotation ignores `pending_dir` entirely.
That latency is deliberate, not an oversight — the alternative (probation topics jump the queue)
was considered and rejected during design.

A section holding fewer topics than it wants slots contributes fewer questions rather than asking
the same topic twice in one paper.

---

## The ladder — `ladder.py`

A level never moves on one result. It takes **two consecutive signals in the same direction**,
with a probation in between.

### Step 1: outcome → signal (`signal_for`)

The two kinds of section carry different evidence, so `TopicOutcomeIn` has two optional fields
with a validator enforcing exactly one:

**Non-DI** (`quant`/`reasoning`/`english`, one question per topic) sends `quadrant`:

| Quadrant | Signal | Why |
|---|---|---|
| `mastered` | `+1` | right, inside the time budget |
| `gap` | `-1` | wrong after real effort — the method isn't there |
| `fragile` | `0` | right but slow — evidence about *pacing*, not level |
| `careless` | `0` | wrong but rushed — same |
| `unreached` | `0` | no evidence at all |

**DI** (one topic, five questions) sends a **0–100 `score`**, banded strictly:

| Score | Signal |
|---|---|
| `> 85` (`DI_PROMOTE_SCORE_THRESHOLD`) | `+1` |
| `40 … 85` inclusive | `0` |
| `< 40` (`DI_DEMOTE_SCORE_THRESHOLD`) | `-1` |

**This module never computes that score.** `evaluation_report` does, and hands it over opaque.
Sitting exactly on a threshold holds — promotion needs *strictly* above.

### Step 2: signal → level (`apply_signal`)

| `pending_dir` | signal | result |
|---|---|---|
| any | `0` | **unchanged** — level and probation both survive |
| `0` | `±1` | open a probation in that direction |
| `+1` | `+1` | **level + 1**, probation cleared |
| `-1` | `-1` | **level − 1**, probation cleared |
| `+1` | `-1` | cancel to `0` |
| `-1` | `+1` | cancel to `0` |

Two properties worth naming:

- **A contradictory signal cancels, it never flips.** Getting a `gap` while on promotion-probation
  returns you to neutral, not to demotion-probation. A level only moves on consistent evidence,
  so `mastered → gap → mastered` ends exactly where it started (with a fresh probation open).
- **A `0` signal holds the probation rather than clearing it.** This is what lets a probation
  survive the ~4 cycles rotation takes to revisit a topic. A `fragile` answer in between changes
  nothing.

**At the bounds**, the level holds but the probation is still *consumed*:
`apply_signal(5, +1, +1) == (5, 0)`. A candidate pinned at level 5 re-earns their probation each
time rather than promoting the instant a level 6 becomes available.

```
level 2, neutral   ──mastered──▶  level 2, promotion pending
                   ──mastered──▶  level 3, neutral
                   ──gap───────▶  level 3, demotion pending
                   ──mastered──▶  level 3, neutral        (cancelled, no move)
```

---

## `update_from_evaluation(result)`

Called by `evaluation_report` **sync + retry, 2–3 attempts** via
`infrastructure/messaging/retry.py:retry_sync_call()`, on the caller's own session.

1. Load rows; `current = rows[0].cycle_version`.
2. **Replay guard:**
   - `result.cycle_version < current` → already applied. Return the current snapshot, change
     nothing. **This is what makes the retry safe** — a retry after a partial failure must not
     move every level a second time.
   - `result.cycle_version > current` → out of order; raise so the retry handles it.
3. `_reconcile_topics` again — picks up topics added to `question_bank` since signup, so they
   start rotating in rather than staying invisible to existing candidates.
4. Per outcome: `signal_for` → `apply_signal` → persist `current_level`, `pending_dir`,
   `mastery_score`, `streak`. An outcome naming a topic the candidate has no row for is skipped
   rather than failing the whole evaluation.
5. `cycle_version += 1` on **every** row of the candidate — it is one per-candidate counter, not
   per topic.
6. `_assemble_next_test` for the new cycle, so the *next* paper exists before this call returns.

### Input contract

```python
class TopicOutcomeIn(BaseModel):
    section: str
    topic: str
    quadrant: Quadrant | None = None   # required for quant/reasoning/english
    score: float | None = None         # required for di, 0-100

class EvaluationResultIn(BaseModel):
    user_id: str
    cycle_version: int          # the cycle just completed
    topic_outcomes: list[TopicOutcomeIn]
```

One row per **topic**, not per question — the ladder moves topics. This replaced the scaffold's
`per_topic_result: dict[str, float]`, which could not express a quadrant. `evaluation_report` has
not been written against it yet.

---

## Things to know before changing this

- **DI's ladder currently has no effect on question selection.** Levels are tracked and updated
  correctly, but no DI set in the bank has a uniform difficulty (all 60 are 1→4 ramps), so a
  promoted DI topic still receives a level-3-equivalent set. This is a data limit, not a code
  one — see [user_test_mapping.md](user_test_mapping.md).
- **`mastery_score` and `streak` are vestigial.** They survive from the scaffold as display
  fields. Nothing reads them to make a decision. Do not start.
- **Nothing is calibrated.** Every bank row is `calibration='model_estimate'`, so `difficulty`
  (the level) is a model guess that this ladder treats as ground truth.
- Adding a column? The migration must be **hand-written** — `alembic revision --autogenerate` is
  unusable in this repo (see CLAUDE.md, "Config and gotchas").
