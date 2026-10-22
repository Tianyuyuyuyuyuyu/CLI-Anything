"""Player build command assembly (pure logic, no subprocess)."""

from __future__ import annotations

_BUILTIN_TARGETS = (
    "StandaloneWindows64",
    "StandaloneWindows",
    "StandaloneLinux64",
    "StandaloneOSX",
    "Android",
    "iOS",
    "WebGL",
    "WSAPlayer",
)


def validate_target(target: str) -> str:
    """Validate a ``-buildTarget`` value (custom targets allowed, warned via flag)."""
    target = (target or "").strip()
    if not target or any(ch.isspace() for ch in target):
        raise RuntimeError(f"Invalid build target '{target}'.")
    return target


def is_builtin_target(target: str) -> bool:
    return target in _BUILTIN_TARGETS


def build_args(
    method: str = "CliBuild.Perform",
    target: str | None = None,
    output: str | None = None,
) -> list[str]:
    """Arguments for ``Unity -batchmode -quit -projectPath ... -executeMethod``."""
    args = ["-executeMethod", method]
    if target:
        args.extend(["-buildTarget", target])
    if output:
        args.extend(["-cliBuildOutput", output])
    return args
