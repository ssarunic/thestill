"""Spec #92 — the resource_list measurement and the runner's supplementary checks."""

from __future__ import annotations

import json
from types import SimpleNamespace

from tests.unit.evals.conftest import VALID_RAW_REPORT, make_judge
from thestill.core.resource_seeds import ResourceSource
from thestill.evals.resource_list_checks import ResourceListProbe, resource_list_metrics
from thestill.evals.rubrics import get_rubric
from thestill.evals.runner import EvalRunner
from thestill.models.podcast import Episode
from thestill.utils.file_storage.local import LocalFileStorage
from thestill.utils.path_manager import PathManager

SUMMARY = (
    "## 8. 📚 Resource List\n"
    "* **Mad Men** (tv): the greatest TV show [12:09](?t=729&cite=c1)\n"
    "* Margin Call (Movie) [44:45](?t=2685&cite=c2)\n"
    "* **Vanguard Bond Funds:** fixed income [49:34]\n"
    "* Ed Elson (Host) [00:10]\n"
)
TRANSCRIPT = {
    "episode_id": "",
    "segments": [
        {"id": 0, "start": 10, "end": 20, "speaker": "Ed Elson", "text": "I'm Ed Elson.", "kind": "content"},
        {"id": 1, "start": 300, "end": 310, "speaker": "Scott", "text": "I watch Mad Men.", "kind": "content"},
        {"id": 2, "start": 2680, "end": 2700, "speaker": "Ed", "text": "Margin Call says it.", "kind": "content"},
    ],
}


def _episode():
    return Episode(
        id="ep-1",
        podcast_id="pod-1",
        external_id="e1",
        title="T",
        description="",
        audio_url="https://example.com/a.mp3",
        summary_path="show/ep_summary.md",
        clean_transcript_json_path="show/ep_cleaned.json",
    )


def _source(tmp_path):
    pm = PathManager(storage_path=str(tmp_path))
    clean = pm.clean_transcript_file("show/ep_cleaned.json")
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_text(json.dumps(TRANSCRIPT), encoding="utf-8")
    return ResourceSource(path_manager=pm, file_storage=LocalFileStorage(str(tmp_path)))


class _Repo:
    def get_episode_anchors(self, episode_id):
        return ["person:ed-elson"]

    def get_entity(self, entity_id):
        from thestill.models.entities import EntityRecord, EntityType

        return EntityRecord(id=entity_id, type=EntityType.PERSON, canonical_name="Ed Elson")

    def list_extracted_names(self, episode_id):
        return [("Margin Call", "topic")]

    def find_mentions(self, *, episode_id, limit):
        mention = SimpleNamespace(surface_form="Margin Call")
        return [SimpleNamespace(mention=mention, entity_canonical_name="Margin Call")]


def test_the_probe_measures_grounding_citations_and_links(tmp_path):
    metrics = ResourceListProbe(_source(tmp_path), _Repo())(_episode(), {"summary": SUMMARY})
    assert metrics["items"] == 4 and metrics["bullets"] == 4 and metrics["anchor_items"] == 1
    assert metrics["contract_rate"] == 0.25  # only the Mad Men bullet
    assert metrics["grounding_rate"] == round(2 / 3, 3)  # Mad Men + Margin Call of 3 non-host items
    assert metrics["citation_accuracy"] == 0.5  # Margin Call near, Mad Men elsewhere
    assert metrics["linked_rate"] == 0.5 and metrics["unlinked"] == ["Mad Men"]
    assert metrics["ungrounded"] == ["Vanguard Bond Funds"]


def test_without_a_repository_there_is_no_linked_rate(tmp_path):
    metrics = ResourceListProbe(_source(tmp_path))(_episode(), {"summary": SUMMARY})
    assert metrics["linked_rate"] is None and metrics["items"] == 4


def test_no_resource_list_measures_zero_with_no_rates():
    metrics = resource_list_metrics(None, bullets=0, contract_bullets=0)
    assert metrics["items"] == 0
    assert metrics["contract_rate"] is metrics["grounding_rate"] is metrics["citation_accuracy"] is None


class TestRunnerSupplementary:
    def _run(self, eval_env, check):
        runner = EvalRunner(
            eval_env.config,
            eval_env.path_manager,
            SimpleNamespace(list_podcasts=lambda: [eval_env.podcast]),
            supplementary={"raw-transcript": {"probe": check}},
        )
        rubric = get_rubric("raw-transcript")
        manifest = runner.run(rubric, make_judge([json.dumps(VALID_RAW_REPORT)]), runner.discover(rubric))
        run_dir = eval_env.path_manager.evaluation_run_dir(manifest.run_id)
        report = json.loads((run_dir / manifest.items[0].report_file).read_text())
        return manifest, report

    def test_a_supplementary_result_is_in_the_report_and_never_gates(self, eval_env):
        manifest, report = self._run(eval_env, lambda episode, texts: {"items": 3, "seen": sorted(texts)})
        assert manifest.counts == {"ok": 1, "failed": 0}
        assert report["checks"] == {"probe": {"items": 3, "seen": ["raw_transcript"]}}
        assert manifest.items[0].checks_ok is None  # the rubric has no deterministic checks

    def test_a_failing_supplementary_check_is_recorded_not_fatal(self, eval_env):
        def boom(episode, texts):
            raise RuntimeError("db down")

        manifest, report = self._run(eval_env, boom)
        assert manifest.counts == {"ok": 1, "failed": 0}
        assert report["checks"]["probe"] == {"error": "RuntimeError: db down"}
