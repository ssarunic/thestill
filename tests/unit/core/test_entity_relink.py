"""Spec #81 Phase 4 — choosing which ReFinED links to re-decide."""

from thestill.core.entity_relink import apply_relink, is_unrelated_surface, plan_relink

ROWS = [
    # (mention_id, episode_id, surface, entity_id, canonical_name), newest episode first
    (1, "ep-new", "healthcare", "company:love", "Love"),
    (2, "ep-new", "Love", "company:love", "Love"),
    (3, "ep-new", "Twitter", "company:twitter", "X (social network)"),
    (4, "ep-mid", "Opus", "company:anthropic-principle", "Anthropic principle"),
    (5, "ep-mid", "Anthropic principle", "company:anthropic-principle", "Anthropic principle"),
    (6, "ep-old", "Elon", "person:elon-musk", "Elon Musk"),
    (7, "ep-old", "ChatGPT", "person:first-officer-aviation", "First officer (aviation)"),
]


def test_unrelated_surface_rule():
    assert is_unrelated_surface("healthcare", "Love")
    assert not is_unrelated_surface("love", "Love")
    assert not is_unrelated_surface("Elon", "Elon Musk")
    assert not is_unrelated_surface("", "Love")


def test_default_picks_only_names_that_do_not_resemble_the_entity():
    plan = plan_relink(ROWS)
    assert plan.mention_ids == [1, 3, 4, 7]  # "Twitter" on X is re-decided too: it comes back if right
    assert plan.episode_ids == ["ep-new", "ep-mid", "ep-old"]
    assert (plan.scanned, plan.skipped_related) == (7, 3)
    assert plan.by_entity["Love"] == 1


def test_all_takes_every_link_in_scope():
    plan = plan_relink(ROWS, unrelated_only=False)
    assert plan.mention_ids == [1, 2, 3, 4, 5, 6, 7] and plan.skipped_related == 0


def test_max_episodes_takes_whole_newest_episodes():
    plan = plan_relink(ROWS, unrelated_only=False, max_episodes=2)
    assert plan.episode_ids == ["ep-new", "ep-mid"]
    assert plan.mention_ids == [1, 2, 3, 4, 5]


def test_an_episode_with_nothing_to_relink_does_not_use_up_the_cap():
    rows = [(1, "ep-a", "Love", "company:love", "Love"), (2, "ep-b", "healthcare", "company:love", "Love")]
    plan = plan_relink(rows, max_episodes=1)
    assert plan.episode_ids == ["ep-b"] and plan.mention_ids == [2]


class _Repo:
    def __init__(self):
        self.reset_batches = []

    def reset_mentions_to_pending(self, ids):
        self.reset_batches.append(list(ids))
        return len(ids)


class _Queue:
    def __init__(self, repo):
        self.repo, self.tasks = repo, []

    def add_task(self, episode_id, stage):
        assert self.repo.reset_batches, "enqueued before the mentions were reset"
        self.tasks.append((episode_id, stage.value))


def test_apply_resets_in_batches_before_enqueueing(monkeypatch):
    import thestill.core.entity_relink as relink

    monkeypatch.setattr(relink, "RESET_BATCH_SIZE", 2)
    repo = _Repo()
    queue = _Queue(repo)
    plan = plan_relink(ROWS, unrelated_only=False)
    assert apply_relink(repo, queue, plan) == 7
    assert repo.reset_batches == [[1, 2], [3, 4], [5, 6], [7]]
    assert queue.tasks == [(ep, "resolve-entities") for ep in ("ep-new", "ep-mid", "ep-old")]
