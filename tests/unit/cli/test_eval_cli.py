# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CliRunner smoke tests for the ``thestill eval`` group (spec #53).

The run/judging library logic is covered in tests/unit/evals/; these
verify the CLI wiring: exit codes, the unpinned-judge warning, the
compare classification banner, and the deprecated wrappers' notice.
"""

import json

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.evals.models import ArtifactRef, JudgeInfo, ManifestItem, RubricInfo, RunManifest
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def tmp_cli_env(tmp_path, monkeypatch):
    """Point the CLI at a fresh tmp DB so context construction succeeds."""
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    SqlitePodcastRepository(db_path=str(storage / "podcasts.db"))
    return storage


def _write_run(storage, run_id, *, model="judge-x", sha="s1", accuracy=7.0):
    manifest = RunManifest(
        run_id=run_id,
        created_at="2026-07-10T12:00:00+00:00",
        rubric=RubricInfo(name="raw-transcript", version="1", prompt_sha256="p" * 64),
        judge=JudgeInfo(provider="mock", model=model, temperature=0.0, pinned=True),
        items=[
            ManifestItem(
                podcast_slug="pod",
                episode_slug="ep",
                artifacts={"raw_transcript": ArtifactRef(path="raw/ep.json", sha256=sha)},
                status="ok",
                scores={"accuracy": accuracy},
                report_file="items/pod_ep.json",
            )
        ],
        counts={"ok": 1, "failed": 0},
    )
    run_dir = storage / "evaluations" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps(manifest.model_dump()), encoding="utf-8")
    return manifest


def test_eval_list_empty(tmp_cli_env):
    res = CliRunner().invoke(main, ["eval", "list"])
    assert res.exit_code == 0
    assert "No eval runs yet" in res.output


def test_eval_list_and_show_render_a_run(tmp_cli_env):
    _write_run(tmp_cli_env, "20260710-120000-raw-transcript-base")
    res = CliRunner().invoke(main, ["eval", "list"])
    assert res.exit_code == 0
    assert "20260710-120000-raw-transcript-base" in res.output

    res = CliRunner().invoke(main, ["eval", "show", "20260710-120000-raw-transcript-base"])
    assert res.exit_code == 0
    assert "raw-transcript v1" in res.output
    assert "accuracy=7.0" in res.output


def test_eval_show_unknown_run_exits_nonzero(tmp_cli_env):
    res = CliRunner().invoke(main, ["eval", "show", "20260710-120000-raw-transcript-nope"])
    assert res.exit_code == 1
    assert "no manifest" in res.output


def test_eval_compare_banner_and_json(tmp_cli_env):
    _write_run(tmp_cli_env, "20260710-120000-raw-transcript-a", model="judge-a")
    _write_run(tmp_cli_env, "20260710-130000-raw-transcript-b", model="judge-b", accuracy=8.0)

    res = CliRunner().invoke(
        main, ["eval", "compare", "20260710-120000-raw-transcript-a", "20260710-130000-raw-transcript-b"]
    )
    assert res.exit_code == 0
    assert "JUDGE COMPARISON" in res.output

    res = CliRunner().invoke(
        main,
        ["eval", "compare", "20260710-120000-raw-transcript-a", "20260710-130000-raw-transcript-b", "--json"],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output[res.output.index("{") :])
    assert payload["classification"] == "judge"
    assert payload["dimensions"][0]["delta"] == pytest.approx(1.0)


def test_eval_run_exits_nonzero_when_items_fail(tmp_cli_env, monkeypatch):
    from tests.unit.evals.conftest import make_episode, make_judge, make_podcast

    episode = make_episode("ep", "pod")
    podcast = make_podcast("pod", [episode])
    failed_manifest = RunManifest(
        run_id="20260710-140000-raw-transcript",
        created_at="2026-07-10T14:00:00+00:00",
        rubric=RubricInfo(name="raw-transcript", version="1", prompt_sha256="p" * 64),
        judge=JudgeInfo(provider="mock", model="judge-x", temperature=0.0, pinned=True),
        items=[ManifestItem(podcast_slug="pod", episode_slug="ep", status="failed", error="boom")],
        counts={"ok": 0, "failed": 1},
    )

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def discover(self, *args, **kwargs):
            return [(podcast, episode)]

        def run(self, *args, on_item=None, **kwargs):
            if on_item:
                on_item(failed_manifest.items[0])
            return failed_manifest

    monkeypatch.setattr("thestill.cli.EvalRunner", FakeRunner)
    monkeypatch.setattr("thestill.cli.resolve_judge", lambda *a, **k: make_judge([]))

    res = CliRunner().invoke(main, ["eval", "run", "--rubric", "raw-transcript"])
    assert res.exit_code == 1
    assert "0 ok, 1 failed" in res.output


def test_eval_run_warns_when_judge_unpinned(tmp_cli_env, monkeypatch):
    from tests.unit.evals.conftest import make_episode, make_judge, make_podcast

    episode = make_episode("ep", "pod")
    podcast = make_podcast("pod", [episode])
    ok_manifest = RunManifest(
        run_id="20260710-150000-raw-transcript",
        created_at="2026-07-10T15:00:00+00:00",
        rubric=RubricInfo(name="raw-transcript", version="1", prompt_sha256="p" * 64),
        judge=JudgeInfo(provider="mock", model="judge-x", temperature=0.0, pinned=False),
        counts={"ok": 0, "failed": 0},
    )

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def discover(self, *args, **kwargs):
            return [(podcast, episode)]

        def run(self, *args, **kwargs):
            return ok_manifest

    monkeypatch.setattr("thestill.cli.EvalRunner", FakeRunner)
    monkeypatch.setattr("thestill.cli.resolve_judge", lambda *a, **k: make_judge([], pinned=False))

    res = CliRunner().invoke(main, ["eval", "run", "--rubric", "raw-transcript"])
    assert res.exit_code == 0
    assert "Judge is not pinned" in res.output


def test_deprecated_wrapper_prints_equivalent_command(tmp_cli_env):
    res = CliRunner().invoke(main, ["evaluate-raw-transcript", "--dry-run"])
    assert "Deprecated" in res.output
    assert "thestill eval run --rubric raw-transcript" in res.output
