"""Only cross-module entry point into `user_topic_mapping` — other modules must call through
here, never `models.py` or `repository.py` directly (see CLAUDE.md, "Architecture rules").

This module owns two things nothing else may write: each topic's **level** and the candidate's
**`cycle_version`**. Both move here and only here, which is what lets a failed or retried
downstream step be replayed without corrupting later cycles.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, TopicMappingError
from app.modules.user_test_mapping.service import UserTestMappingService
from app.modules.user_topic_mapping import __version__
from app.modules.user_topic_mapping.ladder import (
    apply_signal,
    mastery_score_for,
    next_streak,
    signal_for,
)
from app.modules.user_topic_mapping.models import UserTopicMastery
from app.modules.user_topic_mapping.repository import UserTopicMappingRepository
from app.modules.user_topic_mapping.rotation import select_slots
from app.modules.user_topic_mapping.schemas import (
    EvaluationResultIn,
    TopicMastery,
    UserTopicMapOut,
)

_MODULE = "user_topic_mapping"


class UserTopicMappingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = UserTopicMappingRepository(session)
        self.test_mapping = UserTestMappingService(session)

    async def handle_user_signup(self, user_id: str) -> UserTopicMapOut:
        """Seed a baseline row per topic for a newly-signed-up candidate, then sync-call
        `UserTestMappingService.on_topic_change` on this same session so the candidate's first
        test is assembled before `POST /v1/tests/start` is hit. Triggered by both
        `POST /v1/users/signup` and the `algoaptitude-user-signup.fifo` SQS consumer (see
        CLAUDE.md, "End-to-end flow").

        Idempotent: seeding is `ON CONFLICT DO NOTHING` and test assembly is keyed on
        (user_id, cycle_version), so a duplicate signup — the REST call *and* the queue message
        for the same candidate, which is the expected case — is a no-op the second time.
        """
        await self._reconcile_topics(user_id, cycle_version=1)
        rows = await self.repo.get_all(user_id)
        if not rows:
            raise TopicMappingError(
                _MODULE,
                __version__,
                "question_bank has no topics; run `./scripts/run_worker.sh seed_question_bank`",
            )

        await self._assemble_next_test(user_id, rows, cycle_version=rows[0].cycle_version)
        return await self.get_for_user(user_id)

    async def update_from_evaluation(self, result: EvaluationResultIn) -> UserTopicMapOut:
        """Called by `evaluation_report.service` after scoring a completed test, via sync + retry
        (2-3 attempts, see `infrastructure/messaging/retry.py`). Moves each tested topic along
        the ladder, advances the candidate's cycle, and sync-calls `on_topic_change` on this same
        session so the *next* test exists before this call returns.
        """
        rows = await self.repo.get_all(result.user_id)
        if not rows:
            raise NotFoundError(_MODULE, __version__, f"no topic map for user {result.user_id}")

        current = rows[0].cycle_version
        if result.cycle_version < current:
            # Already applied. This is the case that makes `retry_sync_call()` safe: a retry
            # after a partial failure must not move every level a second time.
            return await self.get_for_user(result.user_id)
        if result.cycle_version > current:
            raise TopicMappingError(
                _MODULE,
                __version__,
                f"evaluation for cycle {result.cycle_version} arrived before cycle {current} "
                f"was assembled for user {result.user_id}",
            )

        await self._reconcile_topics(result.user_id, cycle_version=current)
        by_topic = {row.topic: row for row in rows}

        for outcome in result.topic_outcomes:
            row = by_topic.get(outcome.topic)
            if row is None:
                # A topic that is no longer in the bank, or was never seeded. Skip rather than
                # fail the whole evaluation over one stale row.
                continue
            signal = signal_for(outcome)
            level, pending = apply_signal(row.current_level, row.pending_dir, signal)
            await self.repo.apply_outcome(
                result.user_id,
                outcome.topic,
                current_level=level,
                pending_dir=pending,
                mastery_score=mastery_score_for(outcome),
                streak=next_streak(row.streak, signal),
            )

        next_cycle = current + 1
        await self.repo.bump_cycle(result.user_id, next_cycle)

        rows = await self.repo.get_all(result.user_id)
        await self._assemble_next_test(result.user_id, rows, cycle_version=next_cycle)
        return await self.get_for_user(result.user_id)

    async def list_current_cycles(self, user_id: str | None = None) -> dict[str, int]:
        """Current cycle per candidate — `{user_id: cycle_version}`.

        For `evaluation_report`'s reconcile: a candidate scored for cycle N whose map is still on
        cycle N had their post-evaluation task die. Cross-module reads go through services, so
        this is the seam rather than a join onto `user_topic_map`.
        """
        return await self.repo.current_cycles(user_id)

    async def get_for_user(self, user_id: str) -> UserTopicMapOut:
        """Current topic snapshot for a candidate. Read-only; used by reporting-adjacent callers,
        never by another module's repository directly."""
        rows = await self.repo.get_all(user_id)
        if not rows:
            raise NotFoundError(_MODULE, __version__, f"no topic map for user {user_id}")
        return UserTopicMapOut(
            user_id=user_id,
            cycle_version=rows[0].cycle_version,
            topics=[
                TopicMastery(
                    section=row.section,
                    topic=row.topic,
                    current_level=row.current_level,
                    pending_dir=row.pending_dir,
                    mastery_score=row.mastery_score,
                    streak=row.streak,
                )
                for row in rows
            ],
        )

    # ---- internals ----

    async def _reconcile_topics(self, user_id: str, cycle_version: int) -> None:
        """Give the candidate a row for every `(section, topic)` in the bank.

        Run on signup *and* on every evaluation, so topics added to `question_bank` after a
        candidate signed up start being rotated in rather than being invisible to them forever.
        The topic list is read through `user_test_mapping`'s service because that module owns
        `question_bank`.
        """
        topics = await self.test_mapping.list_topics()
        await self.repo.seed_missing(
            user_id,
            cycle_version,
            get_settings().initial_topic_level,
            [(ref.section, ref.topic) for ref in topics],
        )

    async def _assemble_next_test(
        self, user_id: str, rows: list[UserTopicMastery], cycle_version: int
    ) -> None:
        """Pick this cycle's topics, record that they were scheduled, and hand them to
        `user_test_mapping` to resolve into concrete questions.

        Topic selection lives here rather than in `user_test_mapping` because it reads and writes
        `user_topic_map` (`last_cycle`, `times_tested`), which only this module may touch.

        No-op if this cycle's test already exists. Assembly is idempotent on its own, but
        *rotation* is not: without this guard a signup delivered over both REST and SQS would
        advance `last_cycle` twice and the candidate's first test would silently skip five topics.
        """
        if await self.test_mapping.has_test_for_cycle(user_id, cycle_version):
            return

        slots = select_slots(list(rows))
        if not slots:
            raise TopicMappingError(
                _MODULE, __version__, f"no topics available to build a test for user {user_id}"
            )
        await self.repo.mark_scheduled(user_id, [slot.topic for slot in slots], cycle_version)
        await self.test_mapping.on_topic_change(user_id, cycle_version, slots)
