"""Spec #66 — the entity backlog is visible in system stats.

A host without the ``entities`` extra marks every new episode
``skipped_unavailable`` and reports success. Before these fields nothing
surfaced that: prod ran for six weeks with no entity data for new episodes
and ``thestill status``, ``get_status`` and the dashboard all looked healthy.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from thestill.services.stats_service import StatsService


class Repo:
    """Just enough repository for ``get_stats``."""

    def __init__(self, statuses=None, *, implement=True):
        self._statuses = statuses or {}
        if implement:
            self.count_entity_extraction_statuses = lambda: dict(self._statuses)

    def count_episode_states(self):
        keys = ("podcasts_tracked", "episodes_total", "discovered", "downloaded", "downsampled")
        keys += ("transcribed", "cleaned", "summarized", "with_summary_path", "failed")
        return dict.fromkeys(keys, 0)


@pytest.fixture
def service(tmp_path):
    from thestill.utils.path_manager import PathManager

    def build(repo):
        return StatsService(tmp_path, repo, PathManager(str(tmp_path)))

    return build


def test_backlog_and_breakdown_come_from_the_repository(service):
    stats = service(Repo({"complete": 2000, "skipped_unavailable": 480, "none": 3})).get_stats()
    assert stats.episodes_entities_skipped_unavailable == 480
    assert stats.entity_extraction_by_status == {"complete": 2000, "skipped_unavailable": 480, "none": 3}


def test_no_backlog_is_zero_not_missing(service):
    stats = service(Repo({"complete": 5})).get_stats()
    assert stats.episodes_entities_skipped_unavailable == 0


def test_repository_without_the_count_keeps_the_payload_well_formed(service):
    stats = service(Repo(implement=False)).get_stats()
    assert stats.entity_extraction_by_status == {}
    assert stats.episodes_entities_skipped_unavailable == 0


def test_availability_reflects_whether_gliner_is_installed_without_importing_it(service, monkeypatch):
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name == "gliner" else real_find_spec(name, *a)
    )
    assert service(Repo()).get_stats().entity_extraction_available is False

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: object() if name == "gliner" else real_find_spec(name, *a)
    )
    sys.modules.pop("gliner", None)
    assert service(Repo()).get_stats().entity_extraction_available is True
    # A status page must never pay for the torch import.
    assert "gliner" not in sys.modules
