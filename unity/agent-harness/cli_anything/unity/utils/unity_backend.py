"""Unity Editor backend.

Single place that knows how to *find* the Unity Editor binary and how to
*run* it in batch mode. Core modules and the CLI never spawn processes
directly — they call :func:`run_unity`.

Detection order:

1. ``UNITY_BIN`` environment variable (path to Unity.exe / Unity binary,
   or a command name resolvable on PATH).
2. ``PATH`` lookup for common names (``unity``, ``unity.exe``).
3. Well-known Unity Hub install locations (glob, highest version wins).
4. Windows registry entries written by the Unity installer (best effort).

We deliberately do **not** reimplement any editor functionality: every
mutation of a Unity project goes through the real editor binary, matching
the CLI-Anything principle "Use the Real Software — Don't Reimplement It".
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

_ENV_VAR = "UNITY_BIN"

_COMMON_NAMES = ["unity", "unity.exe", "Unity.exe"]

_HUB_GLOBS = {
    "nt": [
        r"C:\Program Files\Unity\Hub\Editor\*\Editor\Unity.exe",
        r"C:\Program Files (x86)\Unity\Hub\Editor\*\Editor\Unity.exe",
        r"C:\Program Files\Unity*\Editor\Unity.exe",
    ],
    "posix": [
        "/Applications/Unity/Hub/Editor/*/Unity.app/Contents/MacOS/Unity",
        os.path.expanduser("~/Unity/Hub/Editor/*/Editor/Unity"),
        "/opt/Unity/Hub/Editor/*/Editor/Unity",
    ],
}

_VERSION_RE = re.compile(r"(20\d{2}\.\d+\.\d+[a-z]\d+)")

DEFAULT_TIMEOUT = 900


def _glob_candidates() -> list[str]:
    patterns = _HUB_GLOBS.get(os.name, [])
    candidates: list[str] = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))
    return candidates


def _registry_candidates() -> list[str]:
    """Best-effort lookup of installer-registered editors (Windows only)."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    candidates: list[str] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key_path = r"Software\Unity Technologies\Installer"
            with winreg.OpenKey(hive, key_path) as root:
                index = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, subkey_name) as subkey:
                            location, _ = winreg.QueryValueEx(subkey, "Location")
                    except OSError:
                        continue
                    exe = Path(str(location)) / "Editor" / "Unity.exe"
                    if exe.is_file():
                        candidates.append(str(exe))
        except OSError:
            continue
    return candidates


def _version_sort_key(path: str) -> tuple:
    match = _VERSION_RE.search(path)
    return (tuple(match.group(1).split(".")) if match else ("",), path)


def find_editor_binary() -> str | None:
    """Locate the Unity Editor binary, or ``None`` if not found."""
    env = os.environ.get(_ENV_VAR)
    if env:
        if os.path.isfile(env):
            return str(Path(env).resolve())
        which = shutil.which(env)
        if which:
            return which

    for name in _COMMON_NAMES:
        found = shutil.which(name)
        if found:
            return found

    candidates = _glob_candidates() + _registry_candidates()
    candidates = [c for c in candidates if os.path.isfile(c)]
    if candidates:
        candidates.sort(key=_version_sort_key, reverse=True)
        return candidates[0]
    return None


def require_editor() -> str:
    """Return the editor binary path or raise with an actionable message."""
    binary = find_editor_binary()
    if binary is None:
        raise RuntimeError(
            "Unity Editor binary not found. Install Unity and ensure it is on "
            "PATH, or set the UNITY_BIN environment variable to the editor "
            "binary, e.g. UNITY_BIN=\"C:\\Program Files\\Unity\\Hub\\Editor\\"
            "2021.3.8f1\\Editor\\Unity.exe\"."
        )
    return binary


def editor_version(binary: str | None = None) -> str | None:
    """Best-effort editor version, parsed from the install path layout.

    Unity Hub installs always place the binary under a version directory
    (``.../2021.3.8f1/Editor/Unity.exe``), so the version is recoverable
    without launching the editor (which would require an activated license).
    """
    path = binary or find_editor_binary()
    if not path:
        return None
    match = _VERSION_RE.search(path)
    return match.group(1) if match else None


def batch_args(
    args: list[str],
    project_path: str | None = None,
    quit_mode: bool = True,
    graphics: bool = False,
    log_file: str | None = None,
) -> list[str]:
    """Assemble a Unity batch-mode command line (without the binary)."""
    cmd = ["-batchmode"]
    if not graphics:
        cmd.append("-nographics")
    if project_path:
        cmd.extend(["-projectPath", str(project_path)])
    if quit_mode:
        cmd.append("-quit")
    if log_file:
        cmd.extend(["-logFile", log_file])
    cmd.extend(args)
    return cmd


def launch_unity(project_path: str | None = None, extra_args: list[str] | None = None) -> dict:
    """Launch the editor GUI (not batch mode) without blocking the caller."""
    binary = require_editor()
    cmd = [binary]
    if project_path:
        cmd.extend(["-projectPath", str(project_path)])
    if extra_args:
        cmd.extend(extra_args)
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        flags = 0
    try:
        proc = subprocess.Popen(cmd, creationflags=flags, close_fds=True)  # type: ignore[arg-type]
    except OSError as exc:
        raise RuntimeError(f"Failed to launch Unity: {exc}") from exc
    return {"pid": proc.pid, "command": cmd}


def run_unity(
    args: list[str],
    project_path: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    quit_mode: bool = True,
    graphics: bool = False,
    log_file: str | None = None,
    capture: bool = True,
) -> dict:
    """Run the Unity Editor binary in batch mode and return a result dict.

    ``-runTests`` calls should pass ``quit_mode=False`` (the test runner
    quits on its own); long builds/tests should raise ``timeout``.
    """
    binary = require_editor()
    cmd = [binary] + batch_args(
        args,
        project_path=project_path,
        quit_mode=quit_mode,
        graphics=graphics,
        log_file=log_file,
    )
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
            cwd=project_path,
        )
        returncode, stdout, stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Unity command timed out after {timeout}s: {' '.join(cmd)}"
        ) from exc

    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "command": cmd,
        "duration_sec": round(time.time() - started, 2),
    }
