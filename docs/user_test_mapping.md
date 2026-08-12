# `user_test_mapping`

Turns topic slots into a concrete paper, and serves it. Owns `question_bank` (the real, curated
1,310-row table) and `user_test_questions`.

This module makes no decisions about *what the candidate should be tested on* — `user_topic_mapping`
decides that and hands over the slots. What lives here is the part that depends on what the bank
actually holds, which turns out to be most of the difficulty.

```
app/modules/user_test_mapping/
  models.py       QuestionBank, UserTestQuestions
  schemas.py      TopicSlot, QuestionOut, SectionQuestions, SelectedSection, ...
  selection.py    pick_question, pick_di_set     pure — the fallback ladder
  repository.py   all SQL for both tables
  service.py      list_topics, on_topic_change, get_for_user
```

---

## What the bank actually contains

Everything below is measured from the live table, and it drives most of this module's design.

| Section | Questions | Topics | Per-topic supply at L1/L2/L3/L4/L5 (median) |
|---|---|---|---|
| `di` | 300 | **3** | n/a — sets, see below |
| `quant` | 340 | 17 | 1 / 6 / 9 / 3 / 1 |
| `reasoning` | 340 | 17 | 1 / 7 / 9 / 2 / 0 |
| `english` | 330 | 17 | 1 / 6 / 9 / 3 / 1 |

Three facts that shaped everything:

1. **`difficulty` is already the 1–5 level.** No mapping table, no new column.
2. **Levels 1 and 5 are nearly empty.** The minimum per-topic supply is **0** at both L1 and L5;
   25 of 54 topics don't span all five levels. An exact-level lookup misses routinely, so the
   fallback ladder below is the normal path, not an error case.
3. **DI questions come in fixed sets of five sharing one chart** — 60 sets, exactly 5 questions
   each — so DI cannot be selected a question at a time. One set = the whole DI section.

`expected_time_seconds` is populated everywhere and rises with difficulty *within* a section
(quant L1 40s → L5 110s), **except English, which is nearly flat** (L1 35s → L5 43s). English
difficulty barely moves the clock.

---

## `list_topics()`

Returns every distinct `(section, topic)` — 54 pairs. Exists so `user_topic_mapping` can seed and
reconcile its rows without reading `question_bank` directly, which the architecture rules forbid.

---

## `on_topic_change(user_id, cycle_version, slots)`

Assembles and persists the paper for a cycle. Called synchronously **on the caller's session**
from `user_topic_mapping` — on signup and after every evaluation. Sharing the session is what lets
`update_from_evaluation` return only once the next paper exists.

1. **Already assembled?** Return it unchanged. A retried call must hand back the same paper, never
   a freshly-rolled one.
2. Build the candidate's **seen sets** — every question id and DI `set_id` ever served, plus the
   cycle each was first served in.
3. Resolve each slot (below). A question filled now is added to `seen_ids` immediately so a later
   slot in the same paper can't pick it again.
4. Persist via `repo.upsert`, `ON CONFLICT (user_id, cycle_version) DO NOTHING`.

### How "already seen" is tracked

Derived by parsing every prior `user_test_questions` row for the candidate — no history table. One
row per cycle holding 20 ids is small enough to parse in Python, and the column is `JSON` rather
than `JSONB` anyway, so a `jsonb_path_query` would buy nothing.

---

## Selection — `selection.py`

### Non-DI: `pick_question`

First hit wins, `ORDER BY id` within a step so a replay rebuilds the identical paper.

| Step | Filter | `selection_fallback` |
|---|---|---|
| 1 | unseen, `difficulty == level` | `exact` |
| 2 | unseen, `difficulty in (level±1)`, nearer level first | `adjacent` |
| 3 | unseen, any difficulty in the topic, nearest level first | `any_level` |
| 4 | any in the topic, **least recently seen** first | `repeat` |

Returns `None` only if the topic has no questions at all.

Every slot records which step produced it, persisted on the row. A paper that drifted off-level is
therefore visible rather than silent — worth checking before concluding the ladder is misbehaving.

**Repeats count toward the ladder normally.** A candidate who has exhausted a topic keeps
progressing rather than freezing.

### DI: `pick_di_set`

The same ladder over *sets*, using each set's rounded mean difficulty as its level:
`exact → adjacent → any_level → any_topic (an unseen set from another DI topic) → repeat`.
The chosen set's five questions are taken in `.q1`–`.q5` order, which is also its difficulty ramp.

> ### DI's level ladder is inert, by data not by design
>
> **No DI set has a uniform difficulty.** 51 of 60 span `{1,2,3,4}`, 8 span `{2,3,4}`, 1 spans
> `{1,2,3,5}` — every set is deliberately a ramp inside one chart. Rounded to a mean, that makes
> **every Bar Charts and Table Charts set level 3**, and most Pie Charts sets level 2.
>
> So asking for a level-4 Bar Charts set is unsatisfiable and degrades to "next unseen set in this
> topic". A promoted DI topic gets the same difficulty of set it got before. The ladder is still
> tracked and updated so the data exists when the bank grows; the code is built for a bank that
> has level variety and degrades gracefully against the one that exists.
>
> In practice **every DI slot records `selection_fallback: "adjacent"` or weaker** — even for a
> brand-new candidate at level 2, since the nearest available set level is 3. Seeing `exact` on a
> DI slot means the bank has changed.
>
> 60 sets ÷ 3 topics = 20 per topic, and DI is tested every 3rd cycle, so a candidate sees ~60
> cycles before any DI set repeats.

---

## What gets persisted

`user_test_questions.sections` is a **resolved id list**, not a selection recipe — the choice
between those was the module's main open question, and resolving at assembly time is what makes
`/v1/tests/start` a pure read.

Real output for a freshly signed-up candidate, abridged to one question per section:

```jsonc
{
  "di": {
    "section": "di", "budget_seconds": 478,
    "set_id": "di.barcharts.001", "topic": "Bar Charts",
    "level": 2,                          // the slot level asked for
    "selection_fallback": "adjacent",    // the set's mean level is 3, so 2 was not satisfiable
    "questions": [
      {"question_id": "di.barcharts.001.q1", "topic": "Bar Charts", "level": 1,
       "expected_time_seconds": 70, "order": 1, "selection_fallback": "adjacent"},
      // ...q2 level 2, q3 level 3, q4 level 3, q5 level 4 — the set's built-in ramp
    ]
  },
  "quant": {
    "section": "quant", "budget_seconds": 286,
    "questions": [
      {"question_id": "quant.average.201", "topic": "Average", "level": 2,
       "expected_time_seconds": 50, "order": 1, "selection_fallback": "exact"}
    ]
  }
}
```

Note the DI section's per-question `level` values (1, 2, 3, 3, 4): a set's questions each keep
their **own** difficulty, while the section-level `level` records what was asked for. That gap
between asked-for and delivered is exactly the DI limitation described above, and
`selection_fallback: "adjacent"` on a brand-new candidate is what it looks like in the data.

**Question content is never denormalized here, and answer keys are never copied anywhere.**
`/v1/tests/start` reads content back from `question_bank`, so there is exactly one place an
answer key lives.

### Section budgets

```python
budget_seconds = ceil(sum(expected_time_seconds) * TIME_BUDGET_SLACK)   # slack default 1.15
```

Computed, not fixed — a section's total time now moves with the candidate's levels. A fresh
level-2 candidate gets `di 478 · quant 286 · reasoning 278 · english 219`, about **21 minutes**
against the prototype's fixed 25:00; it lengthens as they level up.

**The clock itself runs in Node.** This is only the number handed over.

---

## `get_for_user(user_id)` — serves `POST /v1/tests/start`

A pure read of the latest cycle's row, hydrated with question content. Idempotent: calling it
twice returns a byte-identical response. 404 (`NotFoundError`) if the candidate has no signup on
record.

The DB columns and the API schema are **not** 1:1:

| API field | Source | Note |
|---|---|---|
| `options` | `option_a`…`option_d` | `None`s dropped |
| `direction` | `chart_direction` | |
| `expected_time_seconds` | stored on the test row | DB column is nullable; coalesced at assembly |
| `chart` | — | **always null**, see below |
| `answer`, `explanation`, `distractor_rationale_*`, `shortcut_*` | — | **never serialized** |

### DI charts ride on the section, not the question

`SectionQuestions` carries `direction` and `chart_svg` once. A DI set's inline SVG is ~37KB and
all five questions share it, so repeating it per question would add ~150KB of duplicate payload to
every response. A full paper is ~51KB as it stands, 37KB of which is that one chart.

---

## Things to know before changing this

- **`question_bank` is read-only here.** It is loaded by `app/workers/seed_question_bank.py`,
  invoked manually only. Its real owner becomes `question_generation` in Phase 2.
- **The model deliberately omits the table's index names.** `0002` renamed `daily20_questions`
  into `question_bank` and its indexes still carry `daily20_*` names. Never run
  `alembic revision --autogenerate` — it will try to drop and recreate them.
- **`evaluation_report` will need a method here.** Scoring needs `answer` and
  `expected_time_seconds`; reporting needs `prerequisite_concept`, `explanation`, `shortcut_*`.
  The outward schemas strip all of it deliberately. Add a service method rather than letting that
  module read `question_bank` directly.
- **`shortcut_available` is only 30% of the bank** (13% in English), so the planned
  `fragile → "use this shortcut"` action will have no content for most questions.
- If you re-granularize DI by the `concept` column into ~15 topics — the obvious next improvement
  — neither the ladder nor rotation needs a change. It is a data-curation task, plus deciding
  whether a DI section may then show more than one chart.
