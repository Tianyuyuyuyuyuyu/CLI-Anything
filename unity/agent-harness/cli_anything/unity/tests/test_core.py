"""Unit tests for cli-anything-unity.

No Unity Editor binary required. Tests project inspection, editor-script
scaffolding, test-runner arg assembly, NUnit XML parsing, backend binary
detection (via UNITY_BIN), and CLI behaviour through the JSON channel.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.unity.core import build as build_core
from cli_anything.unity.core import editor_script, project as project_core, test_runner
from cli_anything.unity.unity_cli import cli
from cli_anything.unity.utils import unity_backend

MANIFEST = json.dumps(
    {
        "dependencies": {
            "com.unity.render-pipelines.universal": "12.1.10",
            "com.unity.test-framework": "1.1.33",
        }
    }
)

NUNIT_RESULTS = """\
<?xml version="1.0" encoding="utf-8"?>
<test-run id="2" name="MyTests" total="3" passed="1" failed="1" \
skipped="1" inconclusive="0" result="Failed">
  <test-case id="0-1000" name="Passes" fullname="Tests.A.Passes" result="Passed"/>
  <test-case id="0-1001" name="Fails" fullname="Tests.B.Fails" result="Failed">
    <failure><message>Expected 2 but was 3</message></failure>
  </test-case>
  <test-case id="0-1002" name="Skipped" fullname="Tests.C.Skipped" result="Skipped"/>
</test-run>
"""


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def fake_project(tmp_path: Path) -> Path:
    root = tmp_path / "MyProject"
    (root / "Assets" / "Scenes").mkdir(parents=True)
    (root / "ProjectSettings").mkdir()
    (root / "Packages").mkdir()
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2021.3.8f1\nm_EditorVersionWithRevision: 2021.3.8f1 (56dbfdd66951)\n"
    )
    (root / "Packages" / "manifest.json").write_text(MANIFEST)
    (root / "Assets" / "Scenes" / "SampleScene.unity").write_text("scene\n")
    (root / "Assets" / "Scripts").mkdir(parents=True, exist_ok=True)
    (root / "Assets" / "Scripts" / "Game.cs").write_text("class Game {}\n")
    (root / "Assets" / "Editor").mkdir(parents=True, exist_ok=True)
    (root / "Assets" / "Editor" / "MyTool.cs").write_text("static class MyTool {}\n")
    return root


# ── core: project ───────────────────────────────────────────────────────


class TestProjectCore:
    def test_is_project(self, fake_project):
        assert project_core.is_project(fake_project) is True
        assert project_core.is_project(fake_project / "Assets") is False
        assert project_core.is_project(fake_project / "missing") is False

    def test_read_project_version(self, fake_project):
        assert project_core.read_project_version(fake_project) == "2021.3.8f1"
        assert project_core.read_project_version(fake_project / "Assets") is None

    def test_read_packages(self, fake_project):
        packages = project_core.read_packages(fake_project)
        assert packages["com.unity.test-framework"] == "1.1.33"
        assert "com.unity.render-pipelines.universal" in packages

    def test_read_project_info(self, fake_project):
        info = project_core.read_project_info(fake_project)
        assert info["status"] == "ok"
        assert info["editor_version"] == "2021.3.8f1"
        assert info["scene_count"] == 1
        assert info["scenes"] == ["Assets/Scenes/SampleScene.unity"]
        assert info["script_count"] >= 2
        assert info["test_framework_installed"] is True
        assert info["package_count"] == 2

    def test_read_project_info_rejects_non_project(self, tmp_path):
        empty = tmp_path / "Empty"
        empty.mkdir()
        with pytest.raises(RuntimeError, match="Not a Unity project"):
            project_core.read_project_info(empty)


# ── core: editor script ─────────────────────────────────────────────────


class TestEditorScript:
    @pytest.mark.parametrize(
        "method",
        ["Run", "Run; DROP TABLE", "MyApp.Run(", ""],
    )
    def test_validate_rejects_bad(self, method):
        with pytest.raises(RuntimeError, match="Invalid -executeMethod"):
            editor_script.validate_execute_method(method)

    @pytest.mark.parametrize(
        "method", ["MyApp.Run", "CliBuild.Perform", "NS.A.B_C.D2", "a.b.c"]
    )
    def test_validate_accepts_good(self, method):
        assert editor_script.validate_execute_method(method) == method

    def test_has_editor_scripts(self, fake_project):
        assert editor_script.has_editor_scripts(fake_project) is True
        plain = fake_project.parent / "NoEditor"
        (plain / "Assets" / "Scripts").mkdir(parents=True)
        (plain / "Assets" / "Scripts" / "X.cs").write_text("class X {}\n")
        assert editor_script.has_editor_scripts(plain) is False

    def test_scaffold(self, fake_project):
        data = editor_script.scaffold_build_script(fake_project)
        assert data["entry_method"] == "CliBuild.Perform"
        written = Path(data["path"])
        assert written.is_file()
        assert "BuildPipeline.BuildPlayer" in written.read_text(encoding="utf-8")
        with pytest.raises(RuntimeError, match="already exists"):
            editor_script.scaffold_build_script(fake_project)
        data = editor_script.scaffold_build_script(fake_project, force=True)
        assert Path(data["path"]).is_file()

    def test_scaffold_rejects_non_project(self, tmp_path):
        with pytest.raises(RuntimeError, match="Not a Unity project"):
            editor_script.scaffold_build_script(tmp_path)


# ── core: test runner ───────────────────────────────────────────────────


class TestTestRunner:
    def test_validate_platform(self):
        assert test_runner.validate_platform("PlayMode") == "PlayMode"
        with pytest.raises(RuntimeError, match="Invalid test platform"):
            test_runner.validate_platform("Nope")

    def test_run_tests_args(self):
        args = test_runner.run_tests_args("EditMode", "res.xml")
        assert args == ["-runTests", "-testPlatform", "EditMode", "-testResults", "res.xml"]

    def test_run_tests_args_targeted(self):
        args = test_runner.run_tests_args(
            "PlayMode",
            "res.xml",
            test_filter="Tests.MyFixture.MyTest",
            test_category="Smoke",
            run_synchronously=True,
        )
        assert args == [
            "-runTests",
            "-testPlatform",
            "PlayMode",
            "-testResults",
            "res.xml",
            "-testFilter",
            "Tests.MyFixture.MyTest",
            "-testCategory",
            "Smoke",
            "-runSynchronously",
        ]

    def test_exit_code_status(self):
        assert test_runner.exit_code_status(0) == "ok"
        assert test_runner.exit_code_status(2) == "tests_failed"
        assert test_runner.exit_code_status(42) == "error"

    def test_parse_results_xml(self, tmp_path):
        results = tmp_path / "results.xml"
        results.write_text(NUNIT_RESULTS)
        summary = test_runner.parse_results_xml(results)
        assert summary["total"] == 3
        assert summary["passed"] == 1
        assert summary["failed"] == 1
        assert summary["skipped"] == 1
        assert summary["status"] == "tests_failed"
        assert summary["failures"][0]["name"] == "Tests.B.Fails"
        assert "Expected 2 but was 3" in summary["failures"][0]["message"]

    def test_parse_missing_file(self, tmp_path):
        with pytest.raises(RuntimeError, match="not found"):
            test_runner.parse_results_xml(tmp_path / "missing.xml")


# ── core: build args ────────────────────────────────────────────────────


class TestBuildCore:
    def test_validate_target(self):
        assert build_core.validate_target("Android") == "Android"
        with pytest.raises(RuntimeError, match="Invalid build target"):
            build_core.validate_target("with space")

    def test_is_builtin_target(self):
        assert build_core.is_builtin_target("StandaloneWindows64") is True
        assert build_core.is_builtin_target("MyCustomTarget") is False

    def test_build_args(self):
        args = build_core.build_args(target="WebGL", output="Builds/Web")
        assert args == [
            "-executeMethod",
            "CliBuild.Perform",
            "-buildTarget",
            "WebGL",
            "-cliBuildOutput",
            "Builds/Web",
        ]


# ── backend: detection ──────────────────────────────────────────────────


class TestBackendDetection:
    def test_env_var_file(self, tmp_path, monkeypatch):
        fake = tmp_path / "Unity.exe"
        fake.write_bytes(b"MZ")
        monkeypatch.setenv("UNITY_BIN", str(fake))
        assert unity_backend.find_editor_binary() == str(fake.resolve())

    def test_env_var_missing(self, monkeypatch):
        monkeypatch.setenv("UNITY_BIN", str(Path("Z:/does/not/exist/Unity.exe")))
        # falls through PATH/glob; on CI machines likely None, never crashes
        result = unity_backend.find_editor_binary()
        assert result is None or Path(result).is_file()

    def test_editor_version_from_path(self, tmp_path, monkeypatch):
        editor = tmp_path / "2021.3.8f1" / "Editor" / "Unity.exe"
        editor.parent.mkdir(parents=True)
        editor.write_bytes(b"MZ")
        monkeypatch.setenv("UNITY_BIN", str(editor))
        assert unity_backend.editor_version() == "2021.3.8f1"

    def test_require_editor_error_message(self, monkeypatch):
        monkeypatch.delenv("UNITY_BIN", raising=False)
        monkeypatch.setattr(unity_backend, "find_editor_binary", lambda: None)
        with pytest.raises(RuntimeError, match="UNITY_BIN"):
            unity_backend.require_editor()


# ── CLI ─────────────────────────────────────────────────────────────────


class TestCLI:
    def test_engine_info_json(self, runner, tmp_path, monkeypatch):
        fake = tmp_path / "2021.3.8f1" / "Editor" / "Unity.exe"
        fake.parent.mkdir(parents=True)
        fake.write_bytes(b"MZ")
        monkeypatch.setenv("UNITY_BIN", str(fake))
        result = runner.invoke(cli, ["--json", "engine", "info"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["version"] == "2021.3.8f1"

    def test_engine_info_missing(self, runner, monkeypatch):
        monkeypatch.setenv("UNITY_BIN", "Z:/nope/Unity.exe")
        monkeypatch.setattr(unity_backend, "_glob_candidates", lambda: [])
        monkeypatch.setattr(unity_backend, "_registry_candidates", lambda: [])
        result = runner.invoke(cli, ["--json", "engine", "info"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"

    def test_project_info_json(self, runner, fake_project):
        result = runner.invoke(cli, ["--json", "project", "info", str(fake_project)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["editor_version"] == "2021.3.8f1"
        assert data["test_framework_installed"] is True

    def test_project_info_human(self, runner, fake_project):
        result = runner.invoke(cli, ["project", "info", str(fake_project)])
        assert result.exit_code == 0
        assert "[ok]" in result.output

    def test_project_info_not_a_project(self, runner, tmp_path):
        result = runner.invoke(cli, ["--json", "project", "info", str(tmp_path)])
        assert result.exit_code == 1
        assert json.loads(result.output)["status"] == "error"

    def test_project_packages_json(self, runner, fake_project):
        result = runner.invoke(cli, ["--json", "project", "packages", str(fake_project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["packages"]["com.unity.test-framework"] == "1.1.33"

    def test_project_scaffold_json(self, runner, fake_project):
        result = runner.invoke(cli, ["--json", "project", "scaffold", str(fake_project)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["entry_method"] == "CliBuild.Perform"
        assert (fake_project / "Assets" / "Editor" / "CliBuild.cs").is_file()

    def test_execute_rejects_bad_method(self, runner, fake_project):
        result = runner.invoke(cli, ["--json", "execute", str(fake_project), "JustRun"])
        assert result.exit_code == 1
        assert "Invalid -executeMethod" in json.loads(result.output)["message"]

    def test_execute_rejects_non_project(self, runner, tmp_path):
        result = runner.invoke(
            cli, ["--json", "execute", str(tmp_path), "A.B.C"]
        )
        assert result.exit_code == 1

    def test_test_rejects_bad_platform(self, runner, fake_project):
        result = runner.invoke(
            cli, ["--json", "test", str(fake_project), "--platform", "Nightly"]
        )
        assert result.exit_code == 1
        assert "Invalid test platform" in json.loads(result.output)["message"]

    def test_test_requires_test_framework(self, runner, tmp_path):
        root = tmp_path / "NoTF"
        (root / "Assets").mkdir(parents=True)
        (root / "ProjectSettings").mkdir()
        result = runner.invoke(cli, ["--json", "test", str(root)])
        assert result.exit_code == 1
        assert "test-framework" in json.loads(result.output)["message"]

    def test_build_requires_scaffold(self, runner, fake_project):
        result = runner.invoke(cli, ["--json", "build", str(fake_project)])
        assert result.exit_code == 1
        assert "project scaffold" in json.loads(result.output)["message"]

    def test_test_options_pass_through(self, runner, fake_project, monkeypatch):
        captured = {}

        def fake_run_unity(args, **kwargs):
            captured["args"] = args
            return {"returncode": 2, "stdout": "", "stderr": "", "duration_sec": 0.1}

        monkeypatch.setattr(unity_backend, "run_unity", fake_run_unity)
        results_file = fake_project / "TestResults" / "r.xml"
        results_file.parent.mkdir()
        results_file.write_text(NUNIT_RESULTS)
        result = runner.invoke(
            cli,
            ["--json", "test", str(fake_project), "--filter", "Tests.A.Passes",
             "--category", "Smoke", "--sync", "--", "-extraFlag"],
        )
        assert result.exit_code != 0  # NUnit results report a failure -> exit 1
        assert "-testFilter" in captured["args"]
        assert "Tests.A.Passes" in captured["args"]
        assert "-testCategory" in captured["args"]
        assert "-runSynchronously" in captured["args"]
        assert "-extraFlag" in captured["args"]

    def test_execute_extra_passthrough(self, runner, fake_project, monkeypatch):
        captured = {}

        def fake_run_unity(args, **kwargs):
            captured["args"] = args
            return {"returncode": 0, "stdout": "", "stderr": "", "duration_sec": 0.1}

        monkeypatch.setattr(unity_backend, "run_unity", fake_run_unity)
        result = runner.invoke(
            cli,
            ["--json", "execute", str(fake_project), "My.App.Run",
             "--", "-accept-apiupdate"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert "-accept-apiupdate" in captured["args"]
        assert "-executeMethod" in captured["args"]

    def test_engine_return_license(self, runner, monkeypatch):
        captured = {}

        def fake_run_unity(args, **kwargs):
            captured["args"] = args
            return {"returncode": 0, "stdout": "", "stderr": "", "duration_sec": 0.1}

        monkeypatch.setattr(unity_backend, "run_unity", fake_run_unity)
        result = runner.invoke(cli, ["--json", "engine", "return-license"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "ok"
        assert captured["args"] == ["-returnlicense"]

    def test_project_export_rejects_no_assets(self, runner, fake_project):
        result = runner.invoke(
            cli, ["--json", "project", "export", str(fake_project), "out.unitypackage"]
        )
        assert result.exit_code == 2  # click: missing required argument

    def test_project_import_rejects_non_project(self, runner, tmp_path):
        pkg = tmp_path / "thing.unitypackage"
        pkg.write_text("x")
        result = runner.invoke(
            cli, ["--json", "project", "import", str(tmp_path), str(pkg)]
        )
        assert result.exit_code == 1
        assert json.loads(result.output)["status"] == "error"

    def test_build_rejects_bad_target(self, runner, fake_project):
        (fake_project / "Assets" / "Editor" / "CliBuild.cs").write_text("x")
        result = runner.invoke(
            cli, ["--json", "build", str(fake_project), "--target", "bad target"]
        )
        assert result.exit_code == 1
        assert "Invalid build target" in json.loads(result.output)["message"]

    def test_logs_missing_file(self, runner, tmp_path):
        result = runner.invoke(
            cli, ["--json", "logs", "--file", str(tmp_path / "nope.log")]
        )
        assert result.exit_code == 1
        assert json.loads(result.output)["status"] == "error"

    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        for group in ("engine", "project", "execute", "test", "build", "session"):
            assert group in result.output
