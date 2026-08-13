# Architecture — the integration view

How this service is driven from outside. [`../CLAUDE.md`](../CLAUDE.md) is the design record (why
the rules are what they are) and the [per-module docs](README.md) are the internals; this file is
the one to read when you are writing the Nest side and need to know **which endpoint to call, when
to call it, what it costs, and what happens if you call it twice**.

Two things to hold onto before anything else:

- **Nest is the only caller.** This service has no auth, no session, no user table and no clock. It
  knows a candidate only by the `user_id` string Nest sends. Everything about *who* that is —
  auth, college, XP, tiers, leaderboards — stays in Nest/Mongo.
- **`user_id` must be the Candidate `_id`**, the same identifier `AlgoApexService.resolveCandidateId`
  resolves for apex — not the auth user id. Send the wrong one and the candidate silently gets a
  second, empty aptitude history.

---

## 1. The surface, at a glance

Base URL: `APTITUDE_API_URL=http://localhost:8090/v1` (`http://aptitude:8090/v1` inside compose).

| # | Endpoint | Called when | Cost | Idempotent? |
|---|---|---|---|---|
| 1 | `POST /v1/users/signup` | Candidate is created in Nest — **and** as a backfill for candidates who already existed | ~1.5 s (writes 54 rows + assembles a paper) | Yes — second call is a no-op |
| 2 | `POST /v1/tests/start` | Candidate opens the Daily 20 | ~0.5 s (one read) | Yes — byte-identical |
| 3 | `POST /v1/tests/complete` | Candidate submits / the last section clock expires | ~1.4 s (6 queries) | Yes — returns the first report |
| 4 | `GET /v1/reports/{user_id}` | Candidate re-opens a past report | ~0.5 s (one read) | Read-only |
| 5 | `GET /v1/progress/{user_id}` | Progress chart on the dashboard | ~0.5 s (one read) | Read-only |
| 6 | `GET /v1/stats/{user_id}` | Profile stats card | ~0.5 s (two reads) | Read-only |
| 7 | `POST /v1/admin/reconcile` | Cron, every few minutes | proportional to how many are stuck | Yes |
| 8 | `GET /v1/health`, `GET /v1/ready` | Nest's `integration-connectivity.service.ts` | 0.8 ms (no DB) | Read-only |

Plus one queue, `algoaptitude-user-signup.fifo`, which is an alternative delivery path for #1 and
nothing more.

All request bodies are **JSON with snake_case fields** — including `/v1/tests/start`, which takes a
body rather than a query param on purpose, so every DTO on the Nest side follows one convention.
Responses are this service's raw Pydantic models; the `{ success, data }` envelope is Nest's to add.

**Timings are network, not compute.** A bare `SELECT 1` to Neon is 98 ms and the application
contributes about a millisecond, so the numbers above are essentially "how many round-trips does
this endpoint make". Give the aptitude `httpClient` profile the same shape as `algoapex`'s: 300 s
timeout, 2 retries, circuit breaker off, dedup off.

---

## 2. The complete user journey

### 2.1 Signup — a brand-new candidate

Nest creates the candidate, then calls:

```
POST /v1/users/signup
{ "user_id": "665f1c9e2a4b8d0012ab34cd" }
```

Inside, on one session, in this order:

1. **Read the topic catalogue** from `question_bank` (through `user_test_mapping.list_topics()` —
   nothing reads another module's tables directly).
2. **Seed one `user_topic_map` row per topic** — 54 today (`di 3`, `quant 17`, `reasoning 17`,
   `english 17`) — each at `INITIAL_TOPIC_LEVEL = 2`, `pending_dir = 0`, `cycle_version = 1`,
   `last_cycle = 0`. `ON CONFLICT DO NOTHING`.
3. **Pick cycle 1's topics** by strict round-robin (`rotation.select_slots`): order each section's
   topics by `(last_cycle, topic)` and take the first N — 5 for the three big sections, **1 for
   DI**, because one DI slot is a whole five-question `set_id`. Everything sits at `last_cycle = 0`
   at this point, so cycle 1 gets each section's alphabetically-first topics.
4. **Mark those topics scheduled** — `last_cycle = 1`, `times_tested += 1`.
5. **Sync-call `user_test_mapping.on_topic_change`** on the same session, which resolves each
   `(section, topic, level)` slot to a concrete question id and writes one `user_test_questions`
   row for `(user_id, cycle 1)`. Ids and selection provenance only — no question text is copied
   here, and **no answer key is copied anywhere, ever**.

Returns `UserTopicMapOut`: `{ user_id, cycle_version: 1, topics: [54 × {section, topic,
current_level, pending_dir, mastery_score, streak}], section_progress: [4 × {section,
progress_score: 0.0, current_level: null, raw_score: null}] }`.

**Every section reads 0.0 / null at signup, deliberately** — no initial score is assigned before a
candidate has answered anything. Nest should render that as "not yet measured", not as a zero score.

Nest can safely ignore the response body and treat a 200 as "candidate is ready". The one thing
that matters is that **the first paper already exists when this call returns** — `/v1/tests/start`
never assembles anything, so a candidate whose signup never reached this service gets a 404 when
they try to start, not an empty test.

### 2.2 Signup — candidates who were already onboarded

This is the same call. There is no separate backfill endpoint and no migration to write, because
`handle_user_signup` is idempotent by construction:

- seeding is `ON CONFLICT DO NOTHING`, so existing rows are untouched;
- `_assemble_next_test` **checks `has_test_for_cycle` before selecting slots**, so a second call
  does not advance the rotation.

That guard is the reason a duplicate signup is genuinely free. Without it, a candidate whose signup
arrived over both REST *and* SQS — the expected case once both paths are wired — would have
`last_cycle` bumped twice and their first paper would silently skip five topics.

So, for the existing candidate base, pick whichever fits the Nest codebase:

| Approach | What it looks like | Notes |
|---|---|---|
| **Backfill script** (recommended) | Iterate every Candidate, `POST /v1/users/signup` each, ignore 200s | ~1.5 s per candidate, so batch it with modest concurrency. Re-runnable. |
| **Lazy, on first open** | Call signup before `/v1/tests/start`, or catch the start's 404 and signup-then-retry | No batch job, but pays the signup latency on the candidate's first click |
| **Queue fan-out** | Publish one `algoaptitude-user-signup.fifo` message per existing candidate | Same handler, off the request path — see §4 |

All three converge on the same state, and mixing them is safe. A candidate who signed up before a
topic was added to `question_bank` is *also* covered: `_reconcile_topics` re-runs on every
evaluation, so new topics start rotating in for existing candidates rather than staying invisible
to them.

The one case that is **not** automatic: candidates who already have aptitude history *elsewhere*
(the in-process Nest MCQ practice topic). Nothing is imported. Everyone starts at level 2 across
all 54 topics. If that history should carry over, it's a separate decision — and it collides with
the naming problem in §7.

### 2.3 Taking the test

```
POST /v1/tests/start
{ "user_id": "..." }
```

A **pure, idempotent read** of the paper assembled in §2.1 — one query, no writes, no clock started.
Call it as many times as the candidate refreshes.

```
UserTestMapOut {
  user_id, cycle_version,
  sections: [
    { section: "di", budget_seconds: 478,
      direction: "The chart below shows…",     ← DI only, once per section
      chart_svg: "<svg …>",                    ← DI only, once per section (~37KB)
      questions: [ { id, section, topic, question_text, options: [4],
                     direction: null, chart: null, expected_time_seconds } × 5 ] },
    { section: "quant",     budget_seconds: 286, questions: [ … × 5 ] },
    { section: "reasoning", budget_seconds: 278, questions: [ … × 5 ] },
    { section: "english",   budget_seconds: 219, questions: [ … × 5 ] },
  ]
}
```

Four things the frontend needs to know:

- **`answer`, `explanation`, `shortcut_*` and `distractor_rationale_*` are absent from this
  response.** Verified end-to-end: none of those keys appear anywhere in the body. Answer keys
  reach the client only in the post-test report.
- **`budget_seconds` is per section and computed, not fixed.** `ceil(Σ expected_time_seconds ×
  TIME_BUDGET_SLACK)`. A fresh level-2 candidate's paper runs ~21 minutes; it lengthens as they
  level up, so the UI must read these numbers rather than hard-code 25:00.
- **The DI chart rides on the section, not the question.** `QuestionOut.chart` is always null;
  render `SectionQuestions.chart_svg` once above all five. Repeating a 37KB SVG per question would
  add ~150KB of duplicate payload to a body that is ~51KB as it stands.
- **Nest owns the clock.** This service never runs a timer. Whatever Node measures is what gets
  scored.

**404** means the candidate has no signup on record — see §2.2 for the fix, and §6 for the error
contract.

### 2.4 Submitting

```
POST /v1/tests/complete
{
  "user_id": "...",
  "cycle_version": 3,                      ← echo the value /v1/tests/start returned
  "answers": [
    { "question_id": "q_1041", "picked": "B", "elapsed_seconds": 47, "unreached": false },
    …one row per question served, all 20…
  ],
  "sections": [                            ← optional but worth sending
    { "section": "quant", "time_used_seconds": 271 }, …
  ]
}
```

Three fields carry the whole evaluation model, so get them right:

- **`picked: null` + `unreached: false` is a deliberate skip**, and scores as *carelessness* — the
  same verdict as answering too fast. That is intentional: a blank with time on the clock is a
  pacing failure, not a knowledge gap.
- **`unreached: true` only when the section clock expired before the candidate saw the question.**
  Do not use it for skips. It is the only value that exempts a question from being scored against
  the candidate.
- **`sections[]` is optional.** Omit it and time-used is taken as the sum of that section's
  `elapsed_seconds`, which under-counts reading time between questions and makes the "rushed with
  time in hand" finding conservative. Send real wall-clock if Node tracked it.

**`cycle_version` is a required part of the contract, not bookkeeping.** It is what makes retries
and duplicate submissions safe. Echo back exactly what `/v1/tests/start` gave you.

On the request path — three reads, three writes, nothing else:

```
1 query   already scored this cycle? ──▶ if yes, return that report, stop
1 query   read the assembled test
1 query   read the answer keys + explanations + shortcuts
pure      classify all 20 into quadrants, build the report   (no I/O)
3 inserts user_answers, evaluation_result, user_reports   (one transaction, ON CONFLICT DO NOTHING)
RETURN    ReportOut                                        (~1.4 s total)
```

and then, **after the response is on the wire**, as a background task on its own session with 2–3
retries:

```
update_from_evaluation
  ├─ move each tested topic along the level ladder
  ├─ cycle_version += 1  (on every row of the candidate)
  ├─ append one user_section_progress point per evaluated section
  ├─ on_topic_change  ──▶ assemble the NEXT paper
  └─ publish evaluation-completed  (best-effort; a queue failure never costs the ladder move)
```

**This ordering is the central design decision of the service.** Nothing needs tomorrow's paper
until the candidate next opens the app, so making them wait ~20 extra Neon round-trips for it —
before they see the thing they actually came for — was the wrong trade. §5 is the price.

The response is the full `ReportOut`, the *same shape* `GET /v1/reports/{user_id}` serves, so Nest
renders one component either way and the candidate never makes a second call to find out how they
did:

```
ReportOut { user_id, cycle_version, headline,
            tiles: [ {quadrant, label, tone, count, blurb} ],       ← counts sum to 20
            section_table: [ {section, section_name, correct, total,
                              time_used_seconds, budget_seconds, note} × 4 ],
            findings: [ {tone, heading, detail} ],                  ← patterns, not per-question
            actions:  [ {heading, detail, tag} ],                   ← ≤ 6, gap-first
            questions:[ ReportQuestionReview × 20 ],                ← the only place answer keys ship
            created_at }
```

Notes for the renderer: **the report never leads with a score** — that is the product, not a
styling choice. The `unreached` tile is omitted entirely when nothing was unreached, so the tiles
shown always add up. Mastered questions carry no shortcut. And when no pattern fires, `findings`
says "Nothing stands out" rather than inventing one.

**Resubmitting the same `cycle_version` returns a byte-identical report** and writes nothing —
which is exactly what makes it safe for Nest to retry a timed-out submit.

### 2.5 Afterwards — reading it back

```
GET /v1/reports/{user_id}                      → the most recent report
GET /v1/reports/{user_id}?cycle_version=3      → that specific past cycle  (404 if it never existed)
GET /v1/progress/{user_id}?tests=10            → the progress chart, 1–50, newest-last
GET /v1/stats/{user_id}                        → the profile stats card
```

Reports are stored **fully rendered**, not re-derived on read: a report is a record of what the
candidate was actually shown, so fixing an explanation in `question_bank` must not silently rewrite
a report they already read.

`/v1/progress` returns one series per section, oldest-to-newest, ready to plot without sorting.
Each point is `{cycle_version, current_level, raw_score, progress_score}` where `progress_score` is
0–100 with each of the five levels owning an equal 20-point slice (L1 0–20 … L5 80–100). A
candidate who exists but has sat no tests gets four **empty** series — a valid answer, not an error.

`/v1/stats` returns `{win_rates: {quant, reasoning, english, di}, tests_taken, questions_solved,
avg_time_per_question_seconds}`. **XP, tier and rank are deliberately absent** — this service has no
concept of any of them, and college affiliation lives only in Nest/Mongo. Nest composes those from
its own gamification logic alongside this response. `questions_solved` counts reached-and-answered
only, and the average excludes unreached questions so their zeros don't drag it down.

### 2.6 The next day

The candidate opens the app and Nest calls `POST /v1/tests/start` again — same endpoint, same body.
The paper it returns is the one built in §2.4's background task, at the candidate's *updated*
levels, on the *next* five topics in rotation. **Signup is never called again.** The loop from §2.3
onwards is the whole steady state.

Levels move slowly and on purpose: a level never moves on one result. It takes two consecutive
signals in the same direction with a probation in between, and a contradictory signal cancels the
probation back to neutral rather than flipping it. With 17 topics and 5 slots, a probation opened
in cycle N resolves in cycle N+4. Set expectations in the UI accordingly — a candidate will not see
a level move most days, and that is the design.

---

## 3. Call sequence, end to end

```
NEST                              APTITUDE (:8090)                    NEON

candidate created
  │
  ├─ POST /v1/users/signup ──────▶ seed 54 topic rows @ L2 ───────────▶ user_topic_map
  │                                select_slots()  round-robin
  │                                mark last_cycle=1, times_tested+1
  │                                on_topic_change ───────────────────▶ user_test_questions
  ◀──────────────── UserTopicMapOut (cycle 1, all sections 0.0)
  ·
  · …candidate opens Daily 20…
  │
  ├─ POST /v1/tests/start ───────▶ pure read + inline content ◀──────── user_test_questions
  ◀──────────────── UserTestMapOut (4 sections, 20 questions, no keys)  + question_bank
  ·
  · …Node runs the clock, 4 section timers…
  │
  ├─ POST /v1/tests/complete ────▶ scored already? ─ yes ─▶ return it
  │                                read test + answer keys
  │                                classify ×20  (pure)
  │                                write answers/results/report ──────▶ 3 tables
  ◀──────────────── ReportOut  ~1.4s   ✦ candidate sees this now ✦
  ·                                 │
  ·                                 └─ background, own session, retry ×2–3:
  ·                                      ladder → cycle+1 → progress point
  ·                                      on_topic_change  (tomorrow's paper)
  ·                                      publish evaluation-completed
  │
  ├─ GET /v1/reports/{id} ───────▶ stored, fully rendered
  ├─ GET /v1/progress/{id} ──────▶ per-section series
  ├─ GET /v1/stats/{id} ─────────▶ win rates + totals
  ·
  └─ cron: POST /v1/admin/reconcile {} ─▶ re-runs any background half that died
```

---

## 4. The SQS path

`algoaptitude-user-signup.fifo` routes to **the same `handle_user_signup`** that
`POST /v1/users/signup` calls — `app/modules/user_topic_mapping/handlers.py`. It is not a different
feature; it is a different delivery mechanism for §2.1.

Envelope is the standard `{event, version, occurred_at, payload}` shape — the same one Nest's
`buildEnvelope()` already produces for the apex queues — with `MessageGroupId = payload["user_id"]`
and `payload.user_id` required.

Nest may relay signup over REST, over SQS, or **both**. Both is safe (that's what the
`has_test_for_cycle` guard in §2.2 exists for) and is the assumption the code is written under. The
practical difference: REST gives you a synchronous 200 you can act on and the seeded state in the
response; SQS gets signup off the request path and survives this service being down. For a bulk
backfill of already-onboarded candidates, SQS is the better shape.

`algoaptitude-evaluation-completed.fifo` is **published** by the post-evaluation background task and
**consumed by nothing** today. It's there for Nest to hook XP/leaderboard/notification work onto
when that's wanted. Publishing is best-effort inside a `try` — a queue failure must not cost the
candidate a ladder move that already landed — and is skipped silently when
`SQS_EVALUATION_COMPLETED_URL` is unset, which is the normal local-dev case.

Both queues must be declared in the shared `infra/elasticmq.conf`
(`fifo = true, contentBasedDeduplication = false`); ElasticMQ won't serve an undeclared queue.

---

## 5. What breaks, and the recovery path

**The one failure mode worth designing around:** the background task from §2.4 dies — process
restart mid-flight, a DB blip outlasting its retries. The candidate is left **scored but not
advanced**: their report exists, their cycle never incremented, and `/v1/tests/start` keeps handing
back the paper they already sat.

```
POST /v1/admin/reconcile { "user_id": "..." }   → fix one candidate
POST /v1/admin/reconcile { }                    → sweep every stuck candidate
```

"Stuck" means *scored for cycle N while the topic map is still on cycle N*. Recovery rebuilds the
ladder input from the stored `evaluation_result` rows (plus the assembled test, for DI's set→topic
grouping) and calls the same `update_from_evaluation` — so it takes the ladder's replay guard, and
a double-run is a no-op returning the current snapshot. Returns `{scanned, reconciled: [{user_id,
cycle_version, action}], skipped}`.

**This endpoint is currently unscheduled and something needs to call it.** It is safe to cron and
has no auth, like everything else here. Every few minutes from Nest or an external scheduler is
enough; without it, a candidate stranded by a dead task stays stranded until someone notices.

The other guard rails, for completeness:

| Guard | What it prevents |
|---|---|
| Replay guard on `update_from_evaluation` | An evaluation for an older cycle is a no-op; a *newer* one raises. This is what makes the sync-retry safe — a retry after a partial failure must not move every level twice. |
| `has_test_for_cycle` before slot selection | A signup arriving over both REST and SQS advancing the rotation twice, silently skipping five topics from the first paper. |
| `ON CONFLICT DO NOTHING` on all three evaluation tables | A resubmit rewriting a report the candidate already read. First submission wins. |
| `ON CONFLICT DO NOTHING` on `user_section_progress` | A retry appending a second chart point, or rewriting one already on screen. |

---

## 6. Errors, and what Nest should do with them

| Status | Meaning | Nest's move |
|---|---|---|
| **404** | Unknown candidate (`/tests/start`, `/reports`, `/progress`, `/stats`), or a `?cycle_version=` that never existed | For an unknown candidate: call `/v1/users/signup` and retry once — see §2.2. Otherwise surface as "no report yet". |
| **422** | Pydantic validation — a malformed body, or a DI outcome row carrying `quadrant` instead of `score` | A bug in the Nest DTO. Do not retry. |
| **500** | A `ModuleError` that is not `NotFoundError` — unseeded question bank, a cycle arriving out of order | Genuinely server-side. Retry is safe (everything is idempotent) but will likely fail identically; alert. |

The body is `{detail, module, version}` on a 404. Note that a 500 here means something structural,
not "the client asked for something absent" — that distinction is why `NotFoundError` exists as its
own class with its own handler in `app/main.py`.

**There is no inbound auth on any endpoint**, including `/v1/admin/reconcile`. That matches the rest
of the stack — Nest sends only `Content-Type` to apex and debug-assessment today — and isolation is
by VPC/compose network. If this service ever becomes reachable from outside that network, `/admin`
is the first thing that needs a guard.

---

## 7. Before the Nest module is written

Two things to settle, neither of which is a code change in this repo:

- **`user_id` must be the Candidate `_id`.** §0. Getting this wrong is silent and expensive to
  unpick later.
- **The name `aptitude` is already taken in Nest.** It is an in-process MCQ practice topic
  (`src/assessments/schemas/assessment.schema.ts:45`, the allowed-topics list in
  `practice-sessions.service.ts:34`) with a global **"Aptitude XP" leaderboard** aggregating
  `topic: 'aptitude'` (`practice-sessions.service.ts:1076-1394`). Either this service takes that
  arena over, or one of the two gets a distinct name. Nothing in *this* repo is blocked by it; the
  Nest module is.

And the mechanical checklist for `src/aptitude/`, mirroring `src/algoapex/`:

- `APTITUDE_API_URL` in `src/config/configuration.ts`, following the `ALGOAPEX_API_URL` form — not
  the legacy `PYTHON_*_SERVICE` one.
- An `httpClient.services.aptitude` profile copying `algoapex`'s: 300 s timeout, 2 retries, circuit
  breaker off, dedup off.
- A REST client through Nest's `HttpClientService` (never raw axios),
  `@Controller('api/aptitude')` under `CandidateAuthGuard`, **snake_case DTO fields** matching the
  Pydantic schemas above, `{ success, data }` response envelope.
- A `checkAptitude()` row in `src/health/integration-connectivity.service.ts`, hitting
  `GET /v1/health` — which returns `{status, modules}` and touches no database, so it's 0.8 ms.
- A scheduled `POST /v1/admin/reconcile {}` — see §5.
