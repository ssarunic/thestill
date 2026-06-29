# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #28 Phase 3 — focused unit tests for the productionisation
sub-tasks 3.1, 3.2, and 3.4.

3.3 (latency budget CI gate) is its own pytest tree under
``tests/perf/`` so a single perf flake doesn't block unrelated unit work.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from thestill.core.queue_manager import ENTITY_BRANCH_STAGES, QueueManager, TaskStage, is_entity_branch_stage
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


def _seed_podcast_with_episodes(db_path: str, statuses: list[str | None]) -> list[str]:
    """Insert a podcast plus one episode per status; return episode ids."""
    podcast_id = str(uuid.uuid4())
    episode_ids = []
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/x.xml", "X", "x"),
        )
        for idx, status in enumerate(statuses):
            eid = str(uuid.uuid4())
            episode_ids.append(eid)
            conn.execute(
                "INSERT INTO episodes "
                "(id, podcast_id, external_id, title, audio_url, entity_extraction_status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (eid, podcast_id, f"ext-{idx}", f"E{idx}", f"https://example.com/e{idx}.mp3", status),
            )
        conn.commit()
    return episode_ids


# ---------------------------------------------------------------------------
# 3.4 — count_episodes_skipped_legacy
# ---------------------------------------------------------------------------


class TestCountEpisodesSkippedLegacy:
    def test_zero_when_no_skipped_episodes(self, tmp_path):
        db_path = str(tmp_path / "x.db")
        repo = SqlitePodcastRepository(db_path=db_path)
        _seed_podcast_with_episodes(db_path, [None, "complete", "pending"])

        assert repo.count_episodes_skipped_legacy() == 0

    def test_counts_only_skipped_legacy(self, tmp_path):
        db_path = str(tmp_path / "x.db")
        repo = SqlitePodcastRepository(db_path=db_path)
        _seed_podcast_with_episodes(
            db_path,
            ["skipped_legacy", "complete", "skipped_legacy", None, "failed", "skipped_legacy"],
        )

        assert repo.count_episodes_skipped_legacy() == 3

    def test_does_not_count_failed_or_pending(self, tmp_path):
        db_path = str(tmp_path / "x.db")
        repo = SqlitePodcastRepository(db_path=db_path)
        _seed_podcast_with_episodes(db_path, ["failed", "pending", "complete"])

        assert repo.count_episodes_skipped_legacy() == 0


# ---------------------------------------------------------------------------
# 3.2 — get_dead_tasks(stage_filter=…)
# ---------------------------------------------------------------------------


class TestDeadTasksStageFilter:
    """``stage_filter`` keeps the entity branch out of the user-pipeline
    DLQ tab and vice versa. The set of entity-branch stages comes from
    the public ``ENTITY_BRANCH_STAGES`` constant — confirm both halves.
    """

    def _seed(self, db_path: str) -> dict[TaskStage, str]:
        """Add one dead task per stage; return {stage: task_id}.

        Episodes are seeded too — the ``tasks`` table has an
        ``episode_id`` FK that fires under ``PRAGMA foreign_keys = ON``
        which the QueueManager connection enables.
        """
        # Seed one podcast + one episode per stage so the FK is satisfied.
        podcast_id = str(uuid.uuid4())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast_id, "https://example.com/x.xml", "X", "x"),
            )
            episode_for_stage: dict[TaskStage, str] = {}
            for stage in TaskStage:
                eid = str(uuid.uuid4())
                episode_for_stage[stage] = eid
                conn.execute(
                    "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) " "VALUES (?, ?, ?, ?, ?)",
                    (eid, podcast_id, f"ext-{stage.value}", f"E-{stage.value}", "https://example.com/e.mp3"),
                )
            conn.commit()
        qm = QueueManager(db_path)
        ids: dict[TaskStage, str] = {}
        for stage in TaskStage:
            t = qm.add_task(episode_id=episode_for_stage[stage], stage=stage)
            qm.mark_dead(t.id, "test failure")
            ids[stage] = t.id
        return ids

    def test_no_filter_returns_all(self, tmp_path):
        db_path = str(tmp_path / "x.db")
        SqlitePodcastRepository(db_path=db_path)
        ids = self._seed(db_path)
        qm = QueueManager(db_path)

        all_dead = qm.get_dead_tasks(limit=100)
        assert {t.id for t in all_dead} == set(ids.values())

    def test_entity_branch_filter(self, tmp_path):
        db_path = str(tmp_path / "x.db")
        SqlitePodcastRepository(db_path=db_path)
        ids = self._seed(db_path)
        qm = QueueManager(db_path)

        entity_only = qm.get_dead_tasks(limit=100, stage_filter=list(ENTITY_BRANCH_STAGES))
        expected = {ids[s] for s in TaskStage if is_entity_branch_stage(s)}
        assert {t.id for t in entity_only} == expected

    def test_user_chain_filter(self, tmp_path):
        db_path = str(tmp_path / "x.db")
        SqlitePodcastRepository(db_path=db_path)
        ids = self._seed(db_path)
        qm = QueueManager(db_path)

        user_chain = [s for s in TaskStage if not is_entity_branch_stage(s)]
        user_only = qm.get_dead_tasks(limit=100, stage_filter=user_chain)
        expected = {ids[s] for s in user_chain}
        assert {t.id for t in user_only} == expected

    def test_empty_stage_filter_returns_all(self, tmp_path):
        """``stage_filter=None`` and ``stage_filter=[]`` both mean
        "no filter" — ``[]`` would otherwise build ``stage IN ()`` which
        is invalid SQL. The implementation skips the clause when falsy.
        """
        db_path = str(tmp_path / "x.db")
        SqlitePodcastRepository(db_path=db_path)
        ids = self._seed(db_path)
        qm = QueueManager(db_path)

        all_dead = qm.get_dead_tasks(limit=100, stage_filter=[])
        assert {t.id for t in all_dead} == set(ids.values())


# ---------------------------------------------------------------------------
# 3.1 — rebuild-entities CLI smoke (uses the click test runner)
# ---------------------------------------------------------------------------


class TestRebuildEntitiesCommand:
    """End-to-end behaviour of ``thestill rebuild-entities``.

    Goes through the click runner so we exercise the full CLI plumbing
    (CLIContext, decorators, podcast service). Asserts that:

    1. ``--dry-run`` doesn't write anything.
    2. A normal run wipes mentions and enqueues ``extract-entities``.
    3. ``has_pending_task`` short-circuit prevents duplicate enqueue.
    """

    @pytest.fixture
    def cli_setup(self, tmp_path, monkeypatch):
        """Spin up a config + repos pointing at a tmp DB and patch
        ``CLIContext`` construction so the command sees them."""
        db_path = str(tmp_path / "podcasts.db")
        # Clean env so config doesn't pick up real OPENAI_API_KEY etc.
        monkeypatch.setenv("DATABASE_PATH", db_path)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
        monkeypatch.setenv("THESTILL_DATA_DIR", str(tmp_path))
        return {"db_path": db_path, "tmp_path": tmp_path}

    def test_dry_run_writes_nothing(self, cli_setup):
        from click.testing import CliRunner

        from thestill.cli import main

        # Seed a podcast + episode the command can find
        db_path = cli_setup["db_path"]
        SqlitePodcastRepository(db_path=db_path)
        with sqlite3.connect(db_path) as conn:
            podcast_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast_id, "https://example.com/x.xml", "Pod", "pod"),
            )
            eid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (eid, podcast_id, "ext-1", "Ep", "https://example.com/e.mp3", "2026-04-15T00:00:00+00:00"),
            )
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["rebuild-entities", "--podcast-id", "pod", "--dry-run"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Eligible episodes: 1" in result.output

        # No tasks should have been enqueued. ``tasks`` table is
        # created lazily by QueueManager; touch it now so the assertion
        # works whether or not the dry-run path created the manager.
        QueueManager(db_path)
        with sqlite3.connect(db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM tasks WHERE stage='extract-entities'").fetchone()[0]
        assert n == 0

    def test_enqueues_extract_entities(self, cli_setup):
        from click.testing import CliRunner

        from thestill.cli import main

        db_path = cli_setup["db_path"]
        SqlitePodcastRepository(db_path=db_path)
        with sqlite3.connect(db_path) as conn:
            podcast_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast_id, "https://example.com/x.xml", "Pod", "pod"),
            )
            eid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date, entity_extraction_status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'complete')",
                (eid, podcast_id, "ext-1", "Ep", "https://example.com/e.mp3", "2026-04-15T00:00:00+00:00"),
            )
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["rebuild-entities", "--podcast-id", "pod"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "1 enqueued" in result.output

        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT episode_id, stage, status FROM tasks WHERE stage='extract-entities'").fetchone()
            assert row is not None
            assert row[0] == eid
            assert row[1] == "extract-entities"
            assert row[2] == "pending"
            # Episode status reset to pending
            status = conn.execute(
                "SELECT entity_extraction_status FROM episodes WHERE id=?",
                (eid,),
            ).fetchone()[0]
            assert status == "pending"

    def test_skips_episode_with_pending_task(self, cli_setup):
        """A pre-existing pending ``extract-entities`` task is honoured —
        we don't double-enqueue.
        """
        from click.testing import CliRunner

        from thestill.cli import main

        db_path = cli_setup["db_path"]
        SqlitePodcastRepository(db_path=db_path)
        with sqlite3.connect(db_path) as conn:
            podcast_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast_id, "https://example.com/x.xml", "Pod", "pod"),
            )
            eid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (eid, podcast_id, "ext-1", "Ep", "https://example.com/e.mp3", "2026-04-15T00:00:00+00:00"),
            )
            conn.commit()

        # Pre-enqueue a pending task
        qm = QueueManager(db_path)
        qm.add_task(episode_id=eid, stage=TaskStage.EXTRACT_ENTITIES)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["rebuild-entities", "--podcast-id", "pod"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "0 enqueued" in result.output
        assert "1 skipped" in result.output

        with sqlite3.connect(db_path) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE stage='extract-entities' AND episode_id=?",
                (eid,),
            ).fetchone()[0]
        assert n == 1


# ---------------------------------------------------------------------------
# DLQ auto-supersedence: stale dead/failed rows disappear when the same
# episode reaches a later stage in the same branch through a fresh run.
# ---------------------------------------------------------------------------


class TestSupersedeStaleTasks:
    """``QueueManager.supersede_stale_tasks`` auto-resolves stale DLQ rows.

    Failure scenario this exists to fix: user has a dead ``transcribe``
    row because their ElevenLabs key was wrong, fixes the key, re-runs
    the pipeline, the episode reaches ``summarize``. The original dead
    transcribe row is now obsolete — Retry on it would race the current
    state — so it should drop out of the DLQ.
    """

    def _seed_episode(self, db_path: str) -> str:
        podcast_id = str(uuid.uuid4())
        eid = str(uuid.uuid4())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast_id, "https://example.com/x.xml", "Pod", "pod"),
            )
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) " "VALUES (?, ?, ?, ?, ?)",
                (eid, podcast_id, "ext-1", "Ep", "https://example.com/e.mp3"),
            )
            conn.commit()
        return eid

    def test_supersedes_earlier_user_chain_stages(self, tmp_path):
        """A successful ``summarize`` supersedes dead rows for any stage
        in ``download..summarize`` for the same episode."""
        db_path = str(tmp_path / "x.db")
        SqlitePodcastRepository(db_path=db_path)
        eid = self._seed_episode(db_path)
        qm = QueueManager(db_path)

        dead_transcribe = qm.add_task(episode_id=eid, stage=TaskStage.TRANSCRIBE)
        qm.mark_dead(dead_transcribe.id, "elevenlabs key bad")
        dead_clean = qm.add_task(episode_id=eid, stage=TaskStage.CLEAN)
        qm.mark_dead(dead_clean.id, "openai 500")

        count = qm.supersede_stale_tasks(eid, TaskStage.SUMMARIZE)
        assert count == 2

        # Both rows are out of the DLQ view.
        assert qm.get_dead_tasks(limit=100) == []

        # Status persisted as ``superseded``.
        assert qm.get_task(dead_transcribe.id).status.value == "superseded"
        assert qm.get_task(dead_clean.id).status.value == "superseded"

    def test_does_not_cross_branches(self, tmp_path):
        """A successful ``summarize`` (user chain) must NOT supersede a
        dead ``extract-entities`` (entity branch). The two are
        independent failure domains by design."""
        db_path = str(tmp_path / "x.db")
        SqlitePodcastRepository(db_path=db_path)
        eid = self._seed_episode(db_path)
        qm = QueueManager(db_path)

        dead_extract = qm.add_task(episode_id=eid, stage=TaskStage.EXTRACT_ENTITIES)
        qm.mark_dead(dead_extract.id, "gliner crashed")

        count = qm.supersede_stale_tasks(eid, TaskStage.SUMMARIZE)
        assert count == 0

        survivors = {t.id for t in qm.get_dead_tasks(limit=100)}
        assert dead_extract.id in survivors

    def test_only_acts_on_terminal_failures(self, tmp_path):
        """Pending and processing rows for the same stage are untouched
        — supersedence only applies to dead/failed terminal states."""
        db_path = str(tmp_path / "x.db")
        SqlitePodcastRepository(db_path=db_path)
        eid = self._seed_episode(db_path)
        qm = QueueManager(db_path)

        pending = qm.add_task(episode_id=eid, stage=TaskStage.TRANSCRIBE)

        count = qm.supersede_stale_tasks(eid, TaskStage.SUMMARIZE)
        assert count == 0
        assert qm.get_task(pending.id).status.value == "pending"

    def test_supersedes_within_entity_branch(self, tmp_path):
        """A successful ``reindex`` supersedes earlier entity-branch
        DLQ rows for the same episode."""
        db_path = str(tmp_path / "x.db")
        SqlitePodcastRepository(db_path=db_path)
        eid = self._seed_episode(db_path)
        qm = QueueManager(db_path)

        dead_extract = qm.add_task(episode_id=eid, stage=TaskStage.EXTRACT_ENTITIES)
        qm.mark_dead(dead_extract.id, "gliner crashed")

        count = qm.supersede_stale_tasks(eid, TaskStage.REINDEX)
        assert count == 1
        assert qm.get_task(dead_extract.id).status.value == "superseded"

    def test_does_not_touch_other_episodes(self, tmp_path):
        """Supersedence is scoped to a single episode_id."""
        db_path = str(tmp_path / "x.db")
        SqlitePodcastRepository(db_path=db_path)
        eid_a = self._seed_episode(db_path)

        # Second episode under the same podcast row.
        eid_b = str(uuid.uuid4())
        with sqlite3.connect(db_path) as conn:
            podcast_id = conn.execute("SELECT id FROM podcasts LIMIT 1").fetchone()[0]
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) " "VALUES (?, ?, ?, ?, ?)",
                (eid_b, podcast_id, "ext-2", "Ep2", "https://example.com/e2.mp3"),
            )
            conn.commit()

        qm = QueueManager(db_path)
        dead_a = qm.add_task(episode_id=eid_a, stage=TaskStage.TRANSCRIBE)
        qm.mark_dead(dead_a.id, "x")
        dead_b = qm.add_task(episode_id=eid_b, stage=TaskStage.TRANSCRIBE)
        qm.mark_dead(dead_b.id, "y")

        qm.supersede_stale_tasks(eid_a, TaskStage.SUMMARIZE)
        assert qm.get_task(dead_a.id).status.value == "superseded"
        assert qm.get_task(dead_b.id).status.value == "dead"


class TestClearEpisodeFailureForStages:
    """``clear_episode_failure_for_stages`` drops the episode-level failure
    banner when a later success makes it moot.

    Failure scenario this fixes: an episode failed at ``transcribe`` (e.g. a
    DNS blip — ``Failed to connect: nodename nor servname provided``), then
    succeeded on a subsequent run. ``supersede_stale_tasks`` clears the dead
    queue row, but the inbox keeps showing the episode as failed (with a
    Retry button) until the ``failed_at_stage`` banner is cleared too.
    """

    def _seed_failed_episode(self, db_path: str, stage: str) -> tuple[SqlitePodcastRepository, str]:
        repo = SqlitePodcastRepository(db_path=db_path)
        podcast_id = str(uuid.uuid4())
        eid = str(uuid.uuid4())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast_id, "https://example.com/x.xml", "Pod", "pod"),
            )
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, ?, ?, ?)",
                (eid, podcast_id, "ext-1", "Ep", "https://example.com/e.mp3"),
            )
            conn.commit()
        repo.mark_episode_failed(eid, stage, "Failed to connect", "transient")
        return repo, eid

    def _failed_stage(self, repo: SqlitePodcastRepository, eid: str) -> str | None:
        return repo.get_episode(eid)[1].failed_at_stage

    def test_clears_when_failed_stage_at_or_before_completed(self, tmp_path):
        """Completing ``transcribe`` clears a ``transcribe`` failure banner."""
        repo, eid = self._seed_failed_episode(str(tmp_path / "x.db"), "transcribe")
        cleared = repo.clear_episode_failure_for_stages(eid, ["download", "downsample", "transcribe"])
        assert cleared is True
        assert self._failed_stage(repo, eid) is None

    def test_does_not_clear_later_stage_failure(self, tmp_path):
        """A ``transcribe`` success must not wipe a ``summarize`` failure
        recorded for a stage that has not been re-run yet."""
        repo, eid = self._seed_failed_episode(str(tmp_path / "x.db"), "summarize")
        cleared = repo.clear_episode_failure_for_stages(eid, ["download", "downsample", "transcribe"])
        assert cleared is False
        assert self._failed_stage(repo, eid) == "summarize"

    def test_no_failure_recorded_is_noop(self, tmp_path):
        repo, eid = self._seed_failed_episode(str(tmp_path / "x.db"), "transcribe")
        repo.clear_episode_failure(eid)
        assert repo.clear_episode_failure_for_stages(eid, ["transcribe"]) is False

    def test_empty_stage_list_is_noop(self, tmp_path):
        repo, eid = self._seed_failed_episode(str(tmp_path / "x.db"), "transcribe")
        assert repo.clear_episode_failure_for_stages(eid, []) is False
        assert self._failed_stage(repo, eid) == "transcribe"
