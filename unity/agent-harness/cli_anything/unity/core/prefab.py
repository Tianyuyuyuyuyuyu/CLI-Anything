"""Prefab inspection and edit command assembly.

Reading: Unity prefabs are serialized YAML text; we parse the document
stream with regexes (no YAML dependency, tolerant of format drift) for
fast, license-free introspection.

Mutating: assembled as ``-executeMethod CliPrefabTools.Apply`` arguments so
the real editor performs the change via ``PrefabUtility`` — the harness
never rewrites prefab YAML itself.
"""

from __future__ import annotations

import re
from pathlib import Path

# Class id -> well-known builtin component name (subset agents care about).
_BUILTIN_COMPONENTS = {
    4: "Transform",
    23: "MeshRenderer",
    33: "MeshFilter",
    65: "BoxCollider",
    135: "SphereCollider",
    136: "CapsuleCollider",
    64: "MeshCollider",
    198: "ParticleSystem",
    82: "AudioSource",
    81: "AudioListener",
    95: "Animator",
    111: "Animation",
    114: "MonoBehaviour",
    108: "Light",
    11400000: "Asset",
}

_DOC_RE = re.compile(r"^--- !u!(\d+) &(\d+)", re.MULTILINE)
_NAME_RE = re.compile(r"^  m_Name: (.*)$", re.MULTILINE)
_SCRIPT_GUID_RE = re.compile(r"m_Script: \{[^}]*guid: ([0-9a-f]{32})")
_STRIP_RE = re.compile(r"^  m_SourcePrefab: \{[^}]*guid: ([0-9a-f]{32})", re.MULTILINE)


def list_prefabs(project_root: str | Path) -> list[str]:
    """All ``*.prefab`` files under Assets/, as project-relative posix paths."""
    root = Path(project_root) / "Assets"
    if not root.is_dir():
        raise RuntimeError(f"Not a Unity project (missing Assets/): {project_root}")
    return sorted(
        p.relative_to(Path(project_root)).as_posix()
        for p in root.rglob("*.prefab")
    )


def parse_prefab_summary(prefab_path: str | Path) -> dict:
    """Regex-based summary of a serialized prefab file."""
    path = Path(prefab_path)
    if not path.is_file():
        raise RuntimeError(f"Prefab file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")

    docs = _DOC_RE.findall(text)
    gameobject_ids = [oid for cls, oid in docs if cls == "1"]
    component_counts: dict[str, int] = {}
    for cls, _oid in docs:
        cls = int(cls)
        if cls == 1:
            continue
        name = _BUILTIN_COMPONENTS.get(cls, f"class-{cls}")
        component_counts[name] = component_counts.get(name, 0) + 1

    script_guids = sorted(set(_SCRIPT_GUID_RE.findall(text)))
    strip_guids = sorted(set(_STRIP_RE.findall(text)))
    names = _NAME_RE.findall(text)
    root_name = names[0].strip() if names else None

    return {
        "status": "ok",
        "path": str(path.resolve()),
        "root_name": root_name,
        "gameobject_count": len(gameobject_ids),
        "components": dict(sorted(component_counts.items())),
        "mono_script_guids": script_guids,
        "nested_prefab_guids": strip_guids,
        "file_size": path.stat().st_size,
    }


def resolve_prefab(project_root: str | Path, asset_path: str) -> Path:
    """Resolve an 'Assets/...' relative path (or direct path) to a file."""
    p = Path(asset_path)
    if not p.is_absolute():
        p = Path(project_root) / asset_path
    if not p.is_file():
        raise RuntimeError(f"Prefab not found: {asset_path} (under {project_root})")
    return p


def edit_args(
    asset_path: str,
    rename: str | None = None,
    set_active: bool | None = None,
    position: tuple[float, float, float] | None = None,
) -> list[str]:
    """Arguments for ``-executeMethod CliPrefabTools.Apply``."""
    args = ["-executeMethod", "CliPrefabTools.Apply", "-prefabPath", asset_path]
    if rename:
        args.extend(["-prefabRename", rename])
    if set_active is not None:
        args.extend(["-prefabSetActive", "true" if set_active else "false"])
    if position:
        args.extend(["-prefabPosition", ",".join(str(v) for v in position)])
    return args
