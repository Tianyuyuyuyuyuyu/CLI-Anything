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
from cli_anything.unity.core import editor_script, prefab as prefab_core, project as project_core, test_runner
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


PREFAB_YAML = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &1234567890
GameObject:
  m_ObjectHideFlags: 0
  m_Name: Hero
  m_IsActive: 1
--- !u!4 &1234567891
Transform:
  m_GameObject: {fileID: 1234567890}
--- !u!114 &1234567892
MonoBehaviour:
  m_GameObject: {fileID: 1234567890}
  m_Script: {fileID: 11500000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}
--- !u!65 &1234567893
BoxCollider:
  m_GameObject: {fileID: 1234567890}
--- !u!1001 &1234567894
PrefabInstance:
  m_SourcePrefab: {fileID: 100100000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb}
"""


# ── core: prefab ────────────────────────────────────────────────────────


class TestPrefabCore:
    @pytest.fixture()
    def prefab_project(self, fake_project):
        prefab_dir = fake_project / "Assets" / "Prefabs"
        prefab_dir.mkdir(parents=True, exist_ok=True)
        (prefab_dir / "Hero.prefab").write_text(PREFAB_YAML)
        (prefab_dir / "Item.prefab").write_text(PREFAB_YAML.replace("Hero", "Item"))
        return fake_project

    def test_list_prefabs(self, prefab_project):
        prefabs = prefab_core.list_prefabs(prefab_project)
        assert prefabs == ["Assets/Prefabs/Hero.prefab", "Assets/Prefabs/Item.prefab"]

    def test_list_prefabs_rejects_non_project(self, tmp_path):
        with pytest.raises(RuntimeError, match="Not a Unity project"):
            prefab_core.list_prefabs(tmp_path)

    def test_parse_prefab_summary(self, prefab_project):
        data = prefab_core.parse_prefab_summary(prefab_project / "Assets" / "Prefabs" / "Hero.prefab")
        assert data["status"] == "ok"
        assert data["root_name"] == "Hero"
        assert data["gameobject_count"] == 1
        assert data["components"]["Transform"] == 1
        assert data["components"]["BoxCollider"] == 1
        assert data["mono_script_guids"] == ["a" * 32]
        assert data["nested_prefab_guids"] == ["b" * 32]

    def test_parse_missing_prefab(self, tmp_path):
        with pytest.raises(RuntimeError, match="not found"):
            prefab_core.parse_prefab_summary(tmp_path / "nope.prefab")

    def test_resolve_prefab(self, prefab_project):
        resolved = prefab_core.resolve_prefab(prefab_project, "Assets/Prefabs/Hero.prefab")
        assert resolved.name == "Hero.prefab"
        with pytest.raises(RuntimeError, match="Prefab not found"):
            prefab_core.resolve_prefab(prefab_project, "Assets/Prefabs/Missing.prefab")

    def test_edit_args(self):
        args = prefab_core.edit_args(
            "Assets/Prefabs/Hero.prefab",
            rename="Boss",
            set_active=False,
            position=(1.0, 2.0, 3.0),
        )
        assert args == [
            "-executeMethod",
            "CliPrefabTools.Apply",
            "-prefabPath",
            "Assets/Prefabs/Hero.prefab",
            "-prefabRename",
            "Boss",
            "-prefabSetActive",
            "false",
            "-prefabPosition",
            "1.0,2.0,3.0",
        ]

    def test_validate_new_prefab_path(self):
        assert prefab_core.validate_new_prefab_path("Assets/Hero.prefab") == "Assets/Hero.prefab"
        assert prefab_core.validate_new_prefab_path("Assets\\Sub\\Hero.prefab") == "Assets/Sub/Hero.prefab"
        for bad in ["Hero.prefab", "Assets/Hero.unity", "Assets/", "Assets/NoExt.pref"]:
            with pytest.raises(RuntimeError, match="Invalid prefab asset path"):
                prefab_core.validate_new_prefab_path(bad)

    def test_create_args(self):
        args = prefab_core.create_args("Assets/Hero.prefab", name="Boss")
        assert args == ["-executeMethod", "CliPrefabTools.Create", "-prefabPath", "Assets/Hero.prefab", "-prefabName", "Boss"]

    def test_create_from_scene_args(self):
        args = prefab_core.create_from_scene_args(
            "Assets/Scenes/Sample.unity", "Parent/Child", "Assets/Child.prefab"
        )
        assert args == [
            "-executeMethod",
            "CliPrefabTools.CreateFromScene",
            "-scenePath",
            "Assets/Scenes/Sample.unity",
            "-gameObjectPath",
            "Parent/Child",
            "-prefabPath",
            "Assets/Child.prefab",
        ]


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

    def test_prefab_list_and_info(self, runner, fake_project):
        prefab_dir = fake_project / "Assets" / "Prefabs"
        prefab_dir.mkdir(parents=True)
        (prefab_dir / "Hero.prefab").write_text(PREFAB_YAML)
        result = runner.invoke(cli, ["--json", "prefab", "list", str(fake_project)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["prefabs"] == ["Assets/Prefabs/Hero.prefab"]
        result = runner.invoke(
            cli, ["--json", "prefab", "info", str(fake_project), "Assets/Prefabs/Hero.prefab"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["root_name"] == "Hero"
        assert data["gameobject_count"] == 1

    def test_prefab_edit_requires_scaffold(self, runner, fake_project):
        prefab_dir = fake_project / "Assets" / "Prefabs"
        prefab_dir.mkdir(parents=True)
        (prefab_dir / "Hero.prefab").write_text(PREFAB_YAML)
        result = runner.invoke(
            cli, ["--json", "prefab", "edit", str(fake_project),
                  "Assets/Prefabs/Hero.prefab", "--rename", "Boss"]
        )
        assert result.exit_code == 1
        assert "prefab scaffold" in json.loads(result.output)["message"]

    def test_prefab_edit_requires_ops(self, runner, fake_project):
        prefab_dir = fake_project / "Assets" / "Prefabs"
        prefab_dir.mkdir(parents=True)
        (prefab_dir / "Hero.prefab").write_text(PREFAB_YAML)
        (fake_project / "Assets" / "Editor").mkdir(exist_ok=True)
        (fake_project / "Assets" / "Editor" / "CliPrefabTools.cs").write_text("x")
        result = runner.invoke(
            cli, ["--json", "prefab", "edit", str(fake_project), "Assets/Prefabs/Hero.prefab"]
        )
        assert result.exit_code == 1
        assert "Nothing to do" in json.loads(result.output)["message"]

    def test_prefab_edit_invalid_position(self, runner, fake_project):
        prefab_dir = fake_project / "Assets" / "Prefabs"
        prefab_dir.mkdir(parents=True)
        (prefab_dir / "Hero.prefab").write_text(PREFAB_YAML)
        (fake_project / "Assets" / "Editor").mkdir(exist_ok=True)
        (fake_project / "Assets" / "Editor" / "CliPrefabTools.cs").write_text("x")
        result = runner.invoke(
            cli, ["--json", "prefab", "edit", str(fake_project),
                  "Assets/Prefabs/Hero.prefab", "--position", "1,2"]
        )
        assert result.exit_code == 1
        assert "Expected 'x,y,z'" in json.loads(result.output)["message"]

    def test_prefab_scaffold_json(self, runner, fake_project):
        result = runner.invoke(cli, ["--json", "prefab", "scaffold", str(fake_project)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["entry_method"] == "CliPrefabTools.Apply"
        written = (fake_project / "Assets" / "Editor" / "CliPrefabTools.cs").read_text(encoding="utf-8")
        assert "public static void Create()" in written
        assert "public static void CreateFromScene()" in written
        assert "SaveAsPrefabAsset" in written
        assert "{{" not in written and "}}" not in written  # braces must render literally

    def test_prefab_create_requires_scaffold(self, runner, fake_project):
        result = runner.invoke(
            cli, ["--json", "prefab", "create", str(fake_project), "Assets/Hero.prefab"]
        )
        assert result.exit_code == 1
        assert "prefab scaffold" in json.loads(result.output)["message"]

    def test_prefab_create_invalid_path(self, runner, fake_project):
        (fake_project / "Assets" / "Editor").mkdir(exist_ok=True)
        (fake_project / "Assets" / "Editor" / "CliPrefabTools.cs").write_text("x")
        result = runner.invoke(
            cli, ["--json", "prefab", "create", str(fake_project), "Hero.prefab"]
        )
        assert result.exit_code == 1
        assert "Invalid prefab asset path" in json.loads(result.output)["message"]

    def test_prefab_create_runs_batch(self, runner, fake_project, monkeypatch):
        captured = {}

        def fake_run_unity(args, **kwargs):
            captured["args"] = args
            return {"returncode": 0, "stdout": "", "stderr": "", "duration_sec": 0.1}

        monkeypatch.setattr(unity_backend, "run_unity", fake_run_unity)
        (fake_project / "Assets" / "Editor").mkdir(exist_ok=True)
        (fake_project / "Assets" / "Editor" / "CliPrefabTools.cs").write_text("x")
        result = runner.invoke(
            cli, ["--json", "prefab", "create", str(fake_project),
                  "Assets/Hero.prefab", "--name", "Boss"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "ok"
        assert captured["args"] == [
            "-executeMethod", "CliPrefabTools.Create",
            "-prefabPath", "Assets/Hero.prefab", "-prefabName", "Boss",
        ]

    def test_prefab_create_from_scene_checks(self, runner, fake_project, monkeypatch):
        captured = {}

        def fake_run_unity(args, **kwargs):
            captured["args"] = args
            return {"returncode": 0, "stdout": "", "stderr": "", "duration_sec": 0.1}

        monkeypatch.setattr(unity_backend, "run_unity", fake_run_unity)
        (fake_project / "Assets" / "Editor").mkdir(exist_ok=True)
        (fake_project / "Assets" / "Editor" / "CliPrefabTools.cs").write_text("x")
        result = runner.invoke(
            cli, ["--json", "prefab", "create-from-scene", str(fake_project),
                  "Assets/Missing.unity", "Hero", "Assets/Hero.prefab"]
        )
        assert result.exit_code == 1
        assert "Scene not found" in json.loads(result.output)["message"]
        scene = fake_project / "Assets" / "Scenes" / "SampleScene.unity"
        scene.parent.mkdir(parents=True, exist_ok=True)
        scene.write_text("scene\n")
        result = runner.invoke(
            cli, ["--json", "prefab", "create-from-scene", str(fake_project),
                  "Assets/Scenes/SampleScene.unity", "Hero", "Assets/Hero.prefab"]
        )
        assert result.exit_code == 0 or "message" in json.loads(result.output)

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
