"""Unity Test Framework command assembly and NUnit3 result parsing."""

from __future__ import annotations

from pathlib import Path

from defusedxml import ElementTree as SafeET

_VALID_PLATFORMS = ("EditMode", "PlayMode")

# Unity Test Framework batch exit codes (docs.unity3d.com Manual/testing).
_TEST_EXIT_CODES = {
    0: "ok",
    2: "tests_failed",
    3: "run_error",
}


def validate_platform(platform: str) -> str:
    platform = (platform or "").strip()
    if platform not in _VALID_PLATFORMS:
        raise RuntimeError(
            f"Invalid test platform '{platform}'. Expected one of: {', '.join(_VALID_PLATFORMS)}."
        )
    return platform


def run_tests_args(
    platform: str = "EditMode",
    results_file: str | None = None,
    test_filter: str | None = None,
    test_category: str | None = None,
    run_synchronously: bool = False,
) -> list[str]:
    """Arguments for ``Unity -batchmode -projectPath ... -runTests``."""
    args = ["-runTests", "-testPlatform", platform]
    if results_file:
        args.extend(["-testResults", str(results_file)])
    if test_filter:
        args.extend(["-testFilter", test_filter])
    if test_category:
        args.extend(["-testCategory", test_category])
    if run_synchronously:
        args.append("-runSynchronously")
    return args


def exit_code_status(returncode: int) -> str:
    return _TEST_EXIT_CODES.get(returncode, "error")


def parse_results_xml(results_file: str | Path) -> dict:
    """Summarize an NUnit3 XML results file produced by ``-testResults``."""
    path = Path(results_file)
    if not path.is_file():
        raise RuntimeError(f"Test results file not found: {path}")
    root = SafeET.parse(str(path)).getroot()
    total = int(root.get("total", "0") or 0)
    passed = int(root.get("passed", "0") or 0)
    failed = int(root.get("failed", "0") or 0)
    skipped = int(root.get("skipped", "0") or 0)
    inconclusive = int(root.get("inconclusive", "0") or 0)
    failures = []
    for case in root.iter("test-case"):
        if case.get("result") == "Failed":
            failures.append(
                {
                    "name": case.get("fullname") or case.get("name"),
                    "message": (case.findtext("failure/message") or "").strip(),
                }
            )
    return {
        "status": "ok" if total == 0 or (failed == 0 and inconclusive == 0) else "tests_failed",
        "results_file": str(path.resolve()),
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "inconclusive": inconclusive,
        "failures": failures,
    }
