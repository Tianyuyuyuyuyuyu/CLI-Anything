"""End-to-end tests for cli-anything-unity.

These require a real Unity Editor installation. Set UNITY_BIN (or pass
--editor) and an activated license; otherwise the whole module is skipped.

Run explicitly with: pytest cli_anything/unity/tests/test_full_e2e.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.unity.unity_cli import cli
from cli_anything.unity.utils import unity_backend

_unity_missing = unity_backend.find_editor_binary() is None
skip_no_unity = pytest.mark.skipif(_unity_missing, reason="Unity Editor binary not found")


def _invoke_json(runner: CliRunner, *args) -> dict:
    result = runner.invoke(cli, ["--json", *args])
    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
    return json.loads(result.output)


@pytest.fixture(scope="module")
def runner() -> CliRunner:
    return CliRunner()


@skip_no_unity
class TestE2EEngine:
    def test_engine_info(self, runner):
        data = _invoke_json(runner, "engine", "info")
        assert data["status"] == "ok"
        assert Path(data["binary"]).is_file()

    def test_engine_version(self, runner):
        data = _invoke_json(runner, "engine", "version")
        assert data["status"] == "ok"
        assert data["version"] and data["version"].startswith("20")


@skip_no_unity
class TestE2EProject:
    def test_project_create_and_info(self, runner, tmp_path):
        target = tmp_path / "E2eProject"
        data = _invoke_json(runner, "project", "create", str(target), "--timeout", "1200")
        assert data["status"] == "ok", data.get("message")
        info = _invoke_json(runner, "project", "info", str(target))
        assert info["status"] == "ok"
        assert info["editor_version"]
        assert info["package_count"] >= 1

    def test_project_scaffold(self, runner, tmp_path):
        target = tmp_path / "ScaffoldProject"
        _invoke_json(runner, "project", "create", str(target), "--timeout", "1200")
        data = _invoke_json(runner, "project", "scaffold", str(target))
        assert data["status"] == "ok"
        assert (Path(data["path"])).is_file()


@skip_no_unity
class TestE2ETest:
    def test_editmode_empty_suite(self, runner, tmp_path):
        target = tmp_path / "TestProject"
        _invoke_json(runner, "project", "create", str(target), "--timeout", "1200")
        data = _invoke_json(runner, "test", str(target), "--platform", "EditMode", "--timeout", "1200")
        assert data["status"] in ("ok", "tests_failed")
        assert data["results_file"] and Path(data["results_file"]).is_file()
