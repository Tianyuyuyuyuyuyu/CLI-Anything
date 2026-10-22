"""cli-anything-unity: Click CLI wrapping the Unity Editor batch mode."""

from __future__ import annotations

import functools
import json as json_mod
import os
import shlex
import sys
from pathlib import Path

import click

from .core import build as build_core
from .core import editor_script, project as project_core, test_runner
from .utils import unity_backend

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
DEFAULT_EDITOR_LOG = (
    str(Path(LOCALAPPDATA) / "Unity" / "Editor" / "Editor.log")
    if LOCALAPPDATA
    else os.path.expanduser("~/.local/share/unity3d/Editor/Editor.log")
)


def _out(ctx, data: dict) -> None:
    """Emit machine (--json) or human readable output."""
    if ctx.obj.get("json"):
        click.echo(json_mod.dumps(data, indent=2, ensure_ascii=False))
        return
    status = data.get("status", "ok")
    if status == "error":
        click.secho(f"Error: {data.get('message', 'unknown error')}", fg="red")
        return
    click.secho(f"[ok] {data.get('action', 'command')}", fg="green", bold=True)
    for key, value in data.items():
        if key in ("status", "action"):
            continue
        if isinstance(value, dict) and len(value) > 6:
            click.echo(f"  {key}: ({len(value)} entries)")
        elif isinstance(value, list) and len(value) > 10:
            click.echo(f"  {key}: ({len(value)} items)")
        else:
            click.echo(f"  {key}: {value}")


def _handle_error(func):
    """Decorator: convert RuntimeError into structured JSON-friendly errors."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        ctx = args[0] if args and isinstance(args[0], click.Context) else None
        try:
            return func(*args, **kwargs)
        except RuntimeError as exc:
            if ctx is not None and ctx.obj.get("repl"):
                click.secho(f"Error: {exc}", fg="red")
                return None
            _out(
                ctx,
                {"status": "error", "message": str(exc), "action": func.__name__},
            )
            sys.exit(1)

    return wrapper


@click.group()
@click.option("--json", "use_json", is_flag=True, help="Emit machine-readable JSON output.")
@click.option("--editor", "editor", default=None, help="Path to the Unity Editor binary (overrides UNITY_BIN).")
@click.pass_context
def cli(ctx, use_json: bool, editor: str | None):
    """Agent-native CLI for the Unity Editor (batch mode wrapper)."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = use_json
    ctx.obj["editor"] = editor


# ── Engine ──────────────────────────────────────────────────────────────


@cli.group()
def engine():
    """Inspect the Unity Editor installation."""


@engine.command("info")
@click.pass_context
@_handle_error
def engine_info(ctx):
    """Locate the Unity Editor binary and report its version."""
    if ctx.obj.get("editor"):
        binary = ctx.obj["editor"]
        if not os.path.isfile(binary):
            raise RuntimeError(f"Editor binary not found: {binary}")
    else:
        binary = unity_backend.find_editor_binary()
        if binary is None:
            raise RuntimeError(
                "Unity Editor binary not found. Set the UNITY_BIN environment "
                "variable or pass --editor."
            )
    _out(
        ctx,
        {
            "status": "ok",
            "action": "engine info",
            "binary": binary,
            "version": unity_backend.editor_version(binary),
            "env_var": unity_backend._ENV_VAR,
        },
    )


@engine.command("version")
@click.pass_context
@_handle_error
def engine_version(ctx):
    """Print the detected editor version string."""
    version = unity_backend.editor_version(ctx.obj.get("editor"))
    if ctx.obj.get("json"):
        _out(ctx, {"status": "ok", "action": "engine version", "version": version})
    else:
        click.echo(version or "unknown")


@engine.command("return-license")
@click.pass_context
@_handle_error
def engine_return_license(ctx):
    """Release the license seat: 'Unity -batchmode -returnlicense'."""
    result = unity_backend.run_unity(["-returnlicense"], log_file="-")
    data = {
        "status": "ok" if result["returncode"] == 0 else "error",
        "action": "engine return-license",
        "returncode": result["returncode"],
        "duration_sec": result["duration_sec"],
    }
    if data["status"] == "error":
        data["message"] = _summarize_unity_failure(result)
    _out(ctx, data)
    if data["status"] == "error" and not ctx.obj.get("repl"):
        sys.exit(1)


# ── Project ─────────────────────────────────────────────────────────────


@cli.group()
def project():
    """Create, inspect, and open Unity projects."""


@project.command("info")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.pass_context
@_handle_error
def project_info(ctx, path: str):
    """Summarize a Unity project (version, packages, assets, scenes)."""
    data = project_core.read_project_info(path)
    data["action"] = "project info"
    _out(ctx, data)


@project.command("create")
@click.argument("path", type=click.Path(file_okay=False))
@click.option("--timeout", default=600, show_default=True, help="Seconds to wait for project creation.")
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
@_handle_error
def project_create(ctx, path: str, timeout: int, extra: tuple[str, ...]):
    """Create a new project via 'Unity -batchmode -createProject'."""
    target = Path(path)
    if (target / "Assets").is_dir():
        raise RuntimeError(f"Path already contains a Unity project: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = unity_backend.run_unity(
        ["-createProject", str(target), *extra],
        timeout=timeout,
        log_file="-",
    )
    data = {
        "status": "ok" if result["returncode"] == 0 and project_core.is_project(target) else "error",
        "action": "project create",
        "path": str(target.resolve()),
        "returncode": result["returncode"],
        "duration_sec": result["duration_sec"],
    }
    if data["status"] == "error":
        data["message"] = _summarize_unity_failure(result)
    _out(ctx, data)
    if data["status"] == "error" and not ctx.obj.get("repl"):
        sys.exit(1)


@project.command("open")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.pass_context
@_handle_error
def project_open(ctx, path: str):
    """Open a project in the editor GUI (non-blocking)."""
    if not project_core.is_project(path):
        raise RuntimeError(f"Not a Unity project (missing Assets/ or ProjectSettings/): {path}")
    result = unity_backend.launch_unity(project_path=path)
    _out(ctx, {"status": "ok", "action": "project open", **result})


@project.command("scaffold")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.option("--force", is_flag=True, help="Overwrite an existing CliBuild.cs.")
@click.pass_context
@_handle_error
def project_scaffold(ctx, path: str, force: bool):
    """Write Assets/Editor/CliBuild.cs build scaffolding into the project."""
    data = editor_script.scaffold_build_script(path, force=force)
    data["status"] = "ok"
    data["action"] = "project scaffold"
    _out(ctx, data)


@project.command("packages")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.pass_context
@_handle_error
def project_packages(ctx, path: str):
    """List direct package dependencies from Packages/manifest.json."""
    packages = project_core.read_packages(path)
    _out(ctx, {"status": "ok", "action": "project packages", "packages": packages})


@project.command("import")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.argument("package", type=click.Path(exists=True, dir_okay=False))
@click.option("--timeout", default=unity_backend.DEFAULT_TIMEOUT, show_default=True)
@click.pass_context
@_handle_error
def project_import(ctx, path: str, package: str, timeout: int):
    """Import a .unitypackage via 'Unity -batchmode -importPackage'."""
    if not project_core.is_project(path):
        raise RuntimeError(f"Not a Unity project: {path}")
    result = unity_backend.run_unity(
        ["-importPackage", str(Path(package).resolve())],
        project_path=path,
        timeout=timeout,
        log_file="-",
    )
    data = {
        "status": "ok" if result["returncode"] == 0 else "error",
        "action": "project import",
        "package": str(package),
        "returncode": result["returncode"],
        "duration_sec": result["duration_sec"],
    }
    if data["status"] == "error":
        data["message"] = _summarize_unity_failure(result)
    _out(ctx, data)
    if data["status"] == "error" and not ctx.obj.get("repl"):
        sys.exit(1)


@project.command("export")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.argument("output", type=click.Path(file_okay=True, dir_okay=False))
@click.argument("asset_paths", nargs=-1, required=True)
@click.option("--timeout", default=unity_backend.DEFAULT_TIMEOUT, show_default=True)
@click.pass_context
@_handle_error
def project_export(ctx, path: str, output: str, asset_paths: tuple[str, ...], timeout: int):
    """Export assets (relative to Assets/) to a .unitypackage.

    Usage: project export PROJECT out.unitypackage Assets/Folder [Assets/File.cs ...]
    """
    if not project_core.is_project(path):
        raise RuntimeError(f"Not a Unity project: {path}")
    out = Path(output)
    if out.parent != Path("."):
        out.parent.mkdir(parents=True, exist_ok=True)
    result = unity_backend.run_unity(
        ["-exportPackage", *asset_paths, str(out.resolve())],
        project_path=path,
        timeout=timeout,
        log_file="-",
    )
    data = {
        "status": "ok" if result["returncode"] == 0 else "error",
        "action": "project export",
        "output": str(out.resolve()),
        "assets": list(asset_paths),
        "returncode": result["returncode"],
        "duration_sec": result["duration_sec"],
    }
    if data["status"] == "error":
        data["message"] = _summarize_unity_failure(result)
    _out(ctx, data)
    if data["status"] == "error" and not ctx.obj.get("repl"):
        sys.exit(1)


# ── Execute ─────────────────────────────────────────────────────────────


@cli.command("execute")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.argument("method")
@click.option("--timeout", default=unity_backend.DEFAULT_TIMEOUT, show_default=True, help="Seconds before the run is killed.")
@click.option("--log-file", default=None, help="Write the editor log to this file instead of stdout.")
@click.option("--graphics", is_flag=True, help="Run with graphics initialized (default: -nographics).")
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
@_handle_error
def execute(ctx, path: str, method: str, timeout: int, log_file: str | None, graphics: bool, extra: tuple[str, ...]):
    """Run a compiled static editor method: 'Namespace.Class.Method'.

    Raw editor flags can be passed through after '--', e.g.:
    execute PROJECT Method -- -accept-apiupdate -disable-assembly-updater
    """
    method = editor_script.validate_execute_method(method)
    if not project_core.is_project(path):
        raise RuntimeError(f"Not a Unity project: {path}")
    result = unity_backend.run_unity(
        ["-executeMethod", method, *extra],
        project_path=path,
        timeout=timeout,
        graphics=graphics,
        log_file=log_file or "-",
    )
    data = {
        "status": "ok" if result["returncode"] == 0 else "error",
        "action": "execute",
        "method": method,
        "returncode": result["returncode"],
        "duration_sec": result["duration_sec"],
        "editor_scripts_present": editor_script.has_editor_scripts(path),
    }
    if data["status"] == "error":
        data["message"] = _summarize_unity_failure(result)
    else:
        tail = [ln for ln in result["stdout"].splitlines() if "CliBuild:" in ln or "error CS" in ln.lower()]
        if tail:
            data["output_tail"] = tail[-10:]
    _out(ctx, data)
    if data["status"] == "error" and not ctx.obj.get("repl"):
        sys.exit(1)


# ── Test ────────────────────────────────────────────────────────────────


@cli.command("test")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.option("--platform", "platform", default="EditMode", show_default=True, help="EditMode or PlayMode.")
@click.option("--results", "results", default=None, help="Where to write the NUnit XML results file.")
@click.option("--timeout", default=unity_backend.DEFAULT_TIMEOUT, show_default=True)
@click.option("--graphics", is_flag=True, help="Run with graphics initialized.")
@click.option("--filter", "test_filter", default=None, help="-testFilter: full name of a test or fixture to run.")
@click.option("--category", "test_category", default=None, help="-testCategory: run only tests in this category.")
@click.option("--sync", "run_synchronously", is_flag=True, help="-runSynchronously (EditMode only; faster).")
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
@_handle_error
def test(ctx, path: str, platform: str, results: str | None, timeout: int, graphics: bool,
         test_filter: str | None, test_category: str | None, run_synchronously: bool,
         extra: tuple[str, ...]):
    """Run Unity Test Framework tests via 'Unity -batchmode -runTests'."""
    platform = test_runner.validate_platform(platform)
    if not project_core.is_project(path):
        raise RuntimeError(f"Not a Unity project: {path}")
    packages = project_core.read_packages(path)
    if "com.unity.test-framework" not in packages:
        raise RuntimeError(
            "Unity Test Framework is not installed in this project "
            "(missing 'com.unity.test-framework' in Packages/manifest.json)."
        )
    results = results or str(Path(path) / "TestResults" / f"cli-{platform.lower()}-results.xml")
    Path(results).parent.mkdir(parents=True, exist_ok=True)
    result = unity_backend.run_unity(
        test_runner.run_tests_args(
            platform,
            results,
            test_filter=test_filter,
            test_category=test_category,
            run_synchronously=run_synchronously,
        ) + list(extra),
        project_path=path,
        timeout=timeout,
        quit_mode=False,
        graphics=graphics,
        log_file="-",
    )
    summary = test_runner.parse_results_xml(results) if Path(results).is_file() else None
    data = {
        "status": summary["status"] if summary else test_runner.exit_code_status(result["returncode"]),
        "action": "test",
        "platform": platform,
        "returncode": result["returncode"],
        "duration_sec": result["duration_sec"],
        "results_file": results,
    }
    if summary:
        data["total"] = summary["total"]
        data["passed"] = summary["passed"]
        data["failed"] = summary["failed"]
        data["skipped"] = summary["skipped"]
        data["failures"] = summary["failures"]
    if data["status"] in ("error", "tests_failed") and summary is None:
        data["message"] = _summarize_unity_failure(result)
    _out(ctx, data)
    if data["status"] in ("error", "tests_failed") and not ctx.obj.get("repl"):
        sys.exit(1)


# ── Build ───────────────────────────────────────────────────────────────


@cli.command("build")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.option("--target", default="StandaloneWindows64", show_default=True, help="Unity build target (e.g. StandaloneWindows64, Android, WebGL).")
@click.option("--output", default=None, help="Player output path (passed to CliBuild as -cliBuildOutput).")
@click.option("--method", default="CliBuild.Perform", show_default=True, help="Static build entry method.")
@click.option("--timeout", default=unity_backend.DEFAULT_TIMEOUT, show_default=True)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
@_handle_error
def build(ctx, path: str, target: str, output: str | None, method: str, timeout: int, extra: tuple[str, ...]):
    """Build a player via -executeMethod (default: scaffolded CliBuild.Perform)."""
    target = build_core.validate_target(target)
    method = editor_script.validate_execute_method(method)
    if not project_core.is_project(path):
        raise RuntimeError(f"Not a Unity project: {path}")
    if method == "CliBuild.Perform" and not (Path(path) / "Assets" / "Editor" / "CliBuild.cs").is_file():
        raise RuntimeError(
            "CliBuild.cs not found. Run 'project scaffold' first, or pass --method "
            "pointing at your own static build method."
        )
    result = unity_backend.run_unity(
        build_core.build_args(method=method, target=target, output=output) + list(extra),
        project_path=path,
        timeout=timeout,
        log_file="-",
    )
    data = {
        "status": "ok" if result["returncode"] == 0 else "error",
        "action": "build",
        "target": target,
        "method": method,
        "returncode": result["returncode"],
        "duration_sec": result["duration_sec"],
    }
    if data["status"] == "error":
        data["message"] = _summarize_unity_failure(result)
    _out(ctx, data)
    if data["status"] == "error" and not ctx.obj.get("repl"):
        sys.exit(1)


# ── Logs ────────────────────────────────────────────────────────────────


@cli.command("logs")
@click.option("--file", "log_file", default=None, help="Log file to read (default: editor Editor.log).")
@click.option("--lines", default=50, show_default=True, help="Number of trailing lines.")
@click.option("--grep", default=None, help="Only show lines containing this string (case-insensitive).")
@click.pass_context
@_handle_error
def logs(ctx, log_file: str | None, lines: int, grep: str | None):
    """Tail the Unity editor log."""
    path = Path(log_file or DEFAULT_EDITOR_LOG)
    if not path.is_file():
        raise RuntimeError(f"Log file not found: {path}. Pass --file to point at a log.")
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if grep:
        needle = grep.lower()
        content = [ln for ln in content if needle in ln.lower()]
    tail = content[-lines:]
    if ctx.obj.get("json"):
        _out(ctx, {"status": "ok", "action": "logs", "file": str(path), "lines": tail})
    else:
        click.echo("\n".join(tail))


# ── REPL session ────────────────────────────────────────────────────────


@cli.command()
@click.pass_context
def session(ctx):
    """Start an interactive REPL session."""
    ctx.obj["repl"] = True

    try:
        from cli_anything.unity.utils.repl_skin import ReplSkin

        skin = ReplSkin("unity", version="1.0.0")
        skin.print_banner()
    except ImportError:
        skin = None
        click.secho("cli-anything-unity REPL", fg="green", bold=True)
        click.echo("Type 'help' for commands, 'exit' to quit.\n")

    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    prompt_session = PromptSession(history=InMemoryHistory())

    while True:
        try:
            prompt_text = (
                skin.prompt(project_name="unity") if skin else "unity> "
            )
            line = prompt_session.prompt(prompt_text)
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if not line:
            continue
        if line in ("exit", "quit", "q"):
            break
        if line == "help":
            click.echo(cli.get_help(click.Context(cli)))
            continue
        try:
            args = shlex.split(line)
        except ValueError as exc:
            click.secho(f"Parse error: {exc}", fg="red")
            continue
        try:
            cli.main(args=args, standalone_mode=False, obj=ctx.obj)
        except SystemExit:
            pass


def _summarize_unity_failure(result: dict) -> str:
    stderr = result.get("stderr") or ""
    stdout = result.get("stdout") or ""
    interesting = [
        ln
        for ln in (stderr.splitlines() + stdout.splitlines())
        if "error" in ln.lower() or "abort" in ln.lower() or "license" in ln.lower()
    ]
    detail = " | ".join(interesting[-3:])
    return (
        f"Unity exited with code {result['returncode']}. {detail}"
        if detail
        else f"Unity exited with code {result['returncode']}."
    )


def main() -> int:
    try:
        cli(obj={})
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
