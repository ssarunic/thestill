"""Spec #28 §2.4 — reindex helpers tested without invoking qmd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from thestill.core.reindex import QmdNotInstalledError, bootstrap_collection, reindex_paths


class TestReindexPaths:
    def test_empty_paths_is_noop(self, tmp_path):
        with patch("thestill.core.reindex.shutil.which", return_value=None):
            report = reindex_paths([])
        assert report["updated"] == 0
        assert report["embedded"] is False
        assert report["skipped"] == []

    def test_filters_non_md_extensions(self, tmp_path):
        # Pass a .segmap.json — should be filtered out before qmd is invoked
        sidecar = tmp_path / "ep.segmap.json"
        sidecar.write_text("[]")
        with patch("thestill.core.reindex.shutil.which", return_value=None):
            report = reindex_paths([sidecar])
        assert report["updated"] == 0

    def test_raises_when_qmd_missing_and_md_files_present(self, tmp_path):
        md = tmp_path / "ep.md"
        md.write_text("# x")
        with patch("thestill.core.reindex.shutil.which", return_value=None):
            with pytest.raises(QmdNotInstalledError):
                reindex_paths([md])

    def test_skips_missing_files(self, tmp_path):
        ghost = tmp_path / "missing.md"  # not created
        with patch("thestill.core.reindex.shutil.which", return_value="/fake/qmd"):
            with patch("thestill.core.reindex.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                report = reindex_paths([ghost])
        assert report["updated"] == 0
        assert str(ghost) in report["skipped"]

    def test_invokes_qmd_update_and_embed(self, tmp_path):
        md = tmp_path / "ep.md"
        md.write_text("# x")
        with patch("thestill.core.reindex.shutil.which", return_value="/fake/qmd"):
            with patch("thestill.core.reindex.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                report = reindex_paths([md], embed=True)
        # update + embed = 2 subprocess calls
        assert run_mock.call_count == 2
        cmds = [call.args[0] for call in run_mock.call_args_list]
        assert any("update" in cmd for cmd in cmds)
        assert any("embed" in cmd for cmd in cmds)
        assert report["updated"] == 1
        assert report["embedded"] is True


class TestBootstrapCollection:
    def test_treats_already_registered_as_success(self, tmp_path):
        with patch("thestill.core.reindex.shutil.which", return_value="/fake/qmd"):
            with patch("thestill.core.reindex.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 1
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = "Collection already exists"
                report = bootstrap_collection(tmp_path)
        assert report["added"] is False

    def test_raises_on_real_failure(self, tmp_path):
        with patch("thestill.core.reindex.shutil.which", return_value="/fake/qmd"):
            with patch("thestill.core.reindex.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 1
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = "permission denied"
                with pytest.raises(RuntimeError):
                    bootstrap_collection(tmp_path)

    def test_passes_path_as_positional(self, tmp_path):
        # qmd CLI expects: ``collection add <path> [--name NAME]`` — argument
        # order matters. Regression test for a real bug from this phase.
        with patch("thestill.core.reindex.shutil.which", return_value="/fake/qmd"):
            with patch("thestill.core.reindex.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""
                bootstrap_collection(tmp_path, collection="mycoll")
        cmd = run_mock.call_args.args[0]
        # The path must come BEFORE --name
        path_idx = cmd.index(str(tmp_path))
        name_idx = cmd.index("--name")
        assert path_idx < name_idx, f"path must precede --name in {cmd}"
        assert cmd[name_idx + 1] == "mycoll"

    def test_raises_when_corpus_dir_missing(self, tmp_path):
        ghost = tmp_path / "no-such-dir"
        with patch("thestill.core.reindex.shutil.which", return_value="/fake/qmd"):
            with pytest.raises(FileNotFoundError):
                bootstrap_collection(ghost)
