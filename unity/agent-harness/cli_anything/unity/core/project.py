"""Pure-logic project inspection.

Reads the on-disk Unity project layout (Assets/, ProjectSettings/,
Packages/) — no subprocess calls, no editor binary required. Used by the
``project`` command group and heavily unit-tested.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

_SCENE_EXT = ".unity"
_SCRIPT_EXTS = {".cs", ".js", ".boo"}
_VERSION_LINE = re.compile(r"^m_EditorVersion:\s*(.+)$", re.MULTILINE)


def is_project(root: str | Path) -> bool:
    """A Unity project minimally has Assets/ and ProjectSettings/."""
    root = Path(root)
    return root.is_dir() and (root / "Assets").is_dir() and (root / "ProjectSettings").is_dir()


def read_project_version(root: str | Path) -> str | None:
    """Parse ``ProjectSettings/ProjectVersion.txt`` -> ``2021.3.8f1``."""
    path = Path(root) / "ProjectSettings" / "ProjectVersion.txt"
    if not path.is_file():
        return None
    match = _VERSION_LINE.search(path.read_text(encoding="utf-8", errors="replace"))
    return match.group(1).strip() if match else None


def read_packages(root: str | Path) -> dict[str, str]:
    """Direct dependency -> version mapping from ``Packages/manifest.json``."""
    path = Path(root) / "Packages" / "manifest.json"
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return dict(manifest.get("dependencies", {}))


def _walk_assets(root: Path, limit: int = 200_000) -> tuple[Counter, list[str]]:
    ext_counts: Counter = Counter()
    scenes: list[str] = []
    count = 0
    for path in (root / "Assets").rglob("*"):
        count += 1
        if count > limit:
            break
        if path.is_file():
            ext_counts[path.suffix.lower() or "<none>"] += 1
            if path.suffix.lower() == _SCENE_EXT:
                scenes.append(path.relative_to(root).as_posix())
    return ext_counts, sorted(scenes)


def read_project_info(root: str | Path) -> dict:
    """Full machine-readable project summary."""
    root = Path(root)
    if not is_project(root):
        raise RuntimeError(f"Not a Unity project (missing Assets/ or ProjectSettings/): {root}")

    ext_counts, scenes = _walk_assets(root)
    script_count = sum(ext_counts.get(ext, 0) for ext in _SCRIPT_EXTS)
    packages = read_packages(root)

    return {
        "status": "ok",
        "path": str(root.resolve()),
        "name": root.resolve().name,
        "editor_version": read_project_version(root),
        "packages": packages,
        "package_count": len(packages),
        "asset_file_count": sum(ext_counts.values()),
        "asset_types": dict(ext_counts.most_common(10)),
        "scene_count": len(scenes),
        "scenes": scenes,
        "script_count": script_count,
        "test_framework_installed": "com.unity.test-framework" in packages,
    }
