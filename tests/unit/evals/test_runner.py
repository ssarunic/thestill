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

"""EvalRunner: judge resolution, discovery, run execution, failure isolation."""

import json

import pytest

from thestill.evals.models import RunManifest
from thestill.evals.rubrics import get_rubric
from thestill.evals.runner import MANIFEST_FILENAME, MAX_INPUT_CHARS, EvalError, EvalRunner, resolve_judge

from .conftest import VALID_CLEAN_REPORT, VALID_RAW_REPORT, make_episode, make_judge


class TestResolveJudge:
    def _config(self, **overrides):
        from types import SimpleNamespace

        defaults = dict(
            eval_judge_provider="",
            eval_judge_model="",
            eval_judge_temperature=0.0,
            llm_provider="openai",
            openai_api_key="sk-test",
            openai_model="gpt-pipeline",
            openai_reasoning_effort=None,
            ollama_base_url="http://localhost:11434",
            ollama_model="gemma3:4b",
            gemini_api_key="",
            gemini_model="gemini-x",
            gemini_thinking_level=None,
            anthropic_api_key="sk-ant-test",
            anthropic_model="claude-config-model",
            mistral_api_key="",
            mistral_model="mistral-x",
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_env_pin_wins_over_pipeline(self):
        config = self._config(eval_judge_provider="anthropic", eval_judge_model="claude-judge-20260101")
        judge = resolve_judge(config)
        assert judge.info.pinned is True
        assert judge.info.provider == "anthropic"
        assert judge.info.model == "claude-judge-20260101"
        assert judge.info.temperature == 0.0

    def test_cli_flag_wins_over_env(self):
        config = self._config(eval_judge_provider="anthropic", eval_judge_model="claude-judge-20260101")
        judge = resolve_judge(config, cli_provider="openai", cli_model="gpt-judge", cli_temperature=0.5)
        assert judge.info.pinned is True
        assert judge.info.provider == "openai"
        assert judge.info.model == "gpt-judge"
        assert judge.info.temperature == 0.5

    def test_fallback_to_pipeline_is_unpinned(self):
        judge = resolve_judge(self._config())
        assert judge.info.pinned is False
        assert judge.info.provider == "openai"
        assert judge.info.model == "gpt-pipeline"

    def test_provider_pin_without_model_uses_family_config_model(self):
        judge = resolve_judge(self._config(eval_judge_provider="anthropic"))
        assert judge.info.pinned is True
        assert judge.info.model == "claude-config-model"

    def test_cli_provider_override_does_not_inherit_env_model_of_other_family(self):
        # .env pins openai/gpt-judge as a coupled pair; a one-off
        # --judge-provider anthropic must get anthropic's config model,
        # not an AnthropicProvider with a gpt-* model id.
        config = self._config(eval_judge_provider="openai", eval_judge_model="gpt-judge-20260101")
        judge = resolve_judge(config, cli_provider="anthropic")
        assert judge.info.provider == "anthropic"
        assert judge.info.model == "claude-config-model"

    def test_env_model_alone_applies_to_pipeline_family_only(self):
        # EVAL_JUDGE_MODEL without EVAL_JUDGE_PROVIDER pins a model within
        # the pipeline's family...
        config = self._config(eval_judge_model="gpt-judge-20260101")
        judge = resolve_judge(config)
        assert (judge.info.provider, judge.info.model) == ("openai", "gpt-judge-20260101")
        # ...but must not leak onto a different family chosen via CLI.
        judge = resolve_judge(config, cli_provider="anthropic")
        assert (judge.info.provider, judge.info.model) == ("anthropic", "claude-config-model")


class TestDiscover:
    def test_requires_all_rubric_inputs_on_disk(self, eval_env):
        # summary rubric needs summary + clean transcript; remove the summary file
        eval_env.path_manager.summary_file(eval_env.episode.summary_path).unlink()
        assert eval_env.runner.discover(get_rubric("summary")) == []
        # clean-transcript rubric is unaffected
        assert len(eval_env.runner.discover(get_rubric("clean-transcript"))) == 1

    def test_episodes_file_pins_subset_and_warns_on_missing(self, eval_env, tmp_path):
        pinned = tmp_path / "golden.json"
        pinned.write_text(
            json.dumps(
                {
                    "episodes": [
                        {"podcast_slug": "test-pod", "episode_slug": "ep-one"},
                        {"podcast_slug": "test-pod", "episode_slug": "ep-gone"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        items = eval_env.runner.discover(get_rubric("raw-transcript"), episodes_file=pinned)
        assert [(p.slug, e.slug) for p, e in items] == [("test-pod", "ep-one")]


class TestRun:
    def test_happy_path_writes_manifest_reports_and_scores(self, eval_env):
        judge = make_judge([json.dumps(VALID_RAW_REPORT)])
        rubric = get_rubric("raw-transcript")
        items = eval_env.runner.discover(rubric)
        manifest = eval_env.runner.run(rubric, judge, items, label="baseline", note="test run")

        assert manifest.counts == {"ok": 1, "failed": 0}
        item = manifest.items[0]
        assert item.scores == {"accuracy": 7.0, "completeness": 9.0, "entity_handling": 8.0, "structural_clarity": 6.0}
        assert item.scores_std is None
        assert item.artifacts["raw_transcript"].sha256
        assert manifest.created_at.endswith("+00:00")
        assert manifest.rubric.prompt_sha256 == rubric.prompt_sha256

        run_dir = eval_env.path_manager.evaluation_run_dir(manifest.run_id)
        persisted = RunManifest.model_validate_json((run_dir / MANIFEST_FILENAME).read_text())
        assert persisted.run_id == manifest.run_id
        report_payload = json.loads((run_dir / item.report_file).read_text())
        assert report_payload["reports"][0]["scores"]["accuracy"] == 7

    def test_invalid_judge_output_retries_once_then_fails_item(self, eval_env):
        # First call: not JSON. Second (retry): schema-violating. -> item failed
        judge = make_judge(["this is not json", json.dumps({"scores": {"accuracy": 99}})])
        rubric = get_rubric("raw-transcript")
        manifest = eval_env.runner.run(rubric, judge, eval_env.runner.discover(rubric))
        assert manifest.counts == {"ok": 0, "failed": 1}
        assert "invalid report twice" in manifest.items[0].error
        # FM-1: the manifest is still written despite the failure
        run_dir = eval_env.path_manager.evaluation_run_dir(manifest.run_id)
        assert (run_dir / MANIFEST_FILENAME).exists()

    def test_control_chars_are_sanitized_before_parse(self, eval_env):
        # FM-7: raw judge output laced with C0 control bytes still parses
        dirty = json.dumps(VALID_RAW_REPORT)[:-1] + "\x00\x07}"
        judge = make_judge([dirty])
        rubric = get_rubric("raw-transcript")
        manifest = eval_env.runner.run(rubric, judge, eval_env.runner.discover(rubric))
        assert manifest.counts["ok"] == 1

    def test_one_bad_episode_does_not_poison_the_run(self, eval_env):
        # Second episode with artifacts on disk
        other = make_episode("ep-two", "test-pod")
        eval_env.podcast.episodes.append(other)
        pm = eval_env.path_manager
        path = pm.raw_transcript_file(other.raw_transcript_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"segments": ["two"]}), encoding="utf-8")

        judge = make_judge(["garbage", "garbage", json.dumps(VALID_RAW_REPORT)])
        rubric = get_rubric("raw-transcript")
        items = eval_env.runner.discover(rubric)
        assert len(items) == 2
        manifest = eval_env.runner.run(rubric, judge, items)
        assert manifest.counts == {"ok": 1, "failed": 1}
        statuses = {item.episode_slug: item.status for item in manifest.items}
        assert set(statuses.values()) == {"ok", "failed"}

    def test_samples_produce_mean_and_std(self, eval_env):
        low = json.dumps(VALID_RAW_REPORT)
        high_report = json.loads(low)
        high_report["scores"]["accuracy"] = 9
        judge = make_judge([low, json.dumps(high_report)])
        rubric = get_rubric("raw-transcript")
        manifest = eval_env.runner.run(rubric, judge, eval_env.runner.discover(rubric), samples=2)
        item = manifest.items[0]
        assert item.scores["accuracy"] == pytest.approx(8.0)
        assert item.scores_std["accuracy"] == pytest.approx(1.414, abs=0.01)
        assert manifest.judge.samples == 2

    def test_oversized_input_is_truncated_and_flagged(self, eval_env):
        pm = eval_env.path_manager
        big = pm.raw_transcript_file(eval_env.episode.raw_transcript_path)
        big.write_text("x" * (MAX_INPUT_CHARS + 10), encoding="utf-8")
        judge = make_judge([json.dumps(VALID_RAW_REPORT)])
        rubric = get_rubric("raw-transcript")
        manifest = eval_env.runner.run(rubric, judge, eval_env.runner.discover(rubric))
        assert manifest.items[0].transcript_truncated is True

    def test_truncation_budget_bounds_the_combined_input(self, eval_env):
        # Two artifacts each just under the cap must still be truncated:
        # the budget is combined, not per-artifact.
        judge = make_judge([json.dumps(VALID_CLEAN_REPORT)])
        rubric = get_rubric("clean-transcript")
        oversized = {
            "clean_transcript": "c" * (MAX_INPUT_CHARS - 10),
            "raw_transcript": "r" * (MAX_INPUT_CHARS - 10),
        }
        _, truncated = eval_env.runner.evaluate_texts(rubric, judge, oversized)
        assert truncated is True
        user_message = judge.provider.last_messages[-1]["content"]
        assert len(user_message) <= MAX_INPUT_CHARS + 200  # small allowance for prompt framing text

    def test_duplicate_run_id_is_refused(self, eval_env, monkeypatch):
        from datetime import datetime, timezone

        rubric = get_rubric("raw-transcript")
        frozen = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        run_id = EvalRunner.build_run_id(rubric.name, None, now=frozen)
        eval_env.path_manager.evaluation_run_dir(run_id).mkdir(parents=True)

        judge = make_judge([])
        monkeypatch.setattr(EvalRunner, "build_run_id", staticmethod(lambda *a, **k: run_id))
        with pytest.raises(EvalError, match="already exists"):
            eval_env.runner.run(rubric, judge, [])

    def test_temperature_omitted_when_provider_rejects_it(self, eval_env):
        judge = make_judge([json.dumps(VALID_RAW_REPORT)], temperature=0.0)
        judge.provider.supports_temperature = lambda: False
        rubric = get_rubric("raw-transcript")
        eval_env.runner.run(rubric, judge, eval_env.runner.discover(rubric))
        assert judge.provider.last_temperature is None
