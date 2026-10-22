# UNITY.md — Harness Architecture

Agent harness for the **Unity Editor** (`Unity.exe` batch mode). Target:
Unity 2019.4+; developed and tested against 2021.3.8f1 on Windows.

## Command Map

| CLI command | Underlying Unity mechanism |
|---|---|
| `engine info` / `engine version` | Binary detection (env/PATH/Hub/registry); version parsed from Hub install path layout |
| `engine return-license` | `Unity -batchmode -quit -returnlicense` |
| `project create <dir>` | `Unity -batchmode -quit -nographics -createProject <dir> -logFile -` |
| `project info` / `project packages` | Read `ProjectSettings/ProjectVersion.txt` and `Packages/manifest.json` directly (no editor launch) |
| `project open` | `Unity -projectPath <dir>` (GUI, detached, non-blocking) |
| `project scaffold` | Writes `Assets/Editor/CliBuild.cs` (standard `BuildPipeline.BuildPlayer` entry point) |
| `project import <project> <pkg>` | `Unity -batchmode -quit -projectPath <dir> -importPackage <pkg> -logFile -` |
| `project export <project> <out> <assets...>` | `Unity -batchmode -quit -projectPath <dir> -exportPackage <assets...> <out> -logFile -` |
| `execute <project> <Method>` | `Unity -batchmode -quit -nographics -projectPath <dir> -executeMethod <Method> -logFile -` |
| `test <project>` | `Unity -batchmode -projectPath <dir> -runTests -testPlatform <Mode> -testResults <xml> [-testFilter/-testCategory/-runSynchronously]`; parses NUnit3 XML |
| `build <project>` | `Unity -batchmode -quit -nographics -projectPath <dir> -buildTarget <T> -executeMethod CliBuild.Perform -cliBuildOutput <path>` |
| `logs` | Tails `%LOCALAPPDATA%\Unity\Editor\Editor.log` (or any `--file`) |
| `-- <raw flags...>` | Verbatim passthrough appended to the Unity command line (e.g. `-accept-apiupdate`, `-disable-assembly-updater`) |

## Deliberately excluded

- `-serial/-username/-password` license activation: credential handling is
  out of scope for the harness; the `--` passthrough covers it, and
  `engine return-license` releases the seat.

## Key Design Decisions

1. **Use the real editor — don't reimplement.** All mutations (create,
   execute, test, build) spawn the actual `Unity.exe` in batch mode. Only
   *reading* (project info, logs) is done with pure file I/O so agents get
   fast, license-free introspection.
2. **No arbitrary C# execution.** Unity only runs compiled static editor
   methods. The harness validates `Class.Method` / `Namespace.Class.Method`
   names and scaffolds `CliBuild.cs` instead of pretending to eval code.
3. **Binary detection with overrides.** `--editor` flag > `UNITY_BIN` env >
   PATH > Unity Hub globs > Windows registry (`Software\Unity Technologies
   \Installer`). Version is parsed from the Hub path layout, never by
   launching the editor (which would fail without a license).
4. **`-logFile -`** streams the editor log to stdout on every batch call;
   failures are summarized (lines containing `error`/`license`) into the
   JSON `message` field.
5. **Test runner semantics.** `-runTests` omits `-quit` (the runner quits
   itself). Exit codes mapped: `0`=ok, `2`=tests_failed, `3`=run_error;
   results XML parsed with `defusedxml`.

## Security

- XML results parsing goes through `defusedxml` (untrusted content never
  touches `xml.etree`).
- No shell interpolation; all subprocess calls use argv lists.
- `project create` refuses paths that already contain a Unity project.

## Version Compatibility

- `-logFile -`, `-createProject`, `-executeMethod`, `-runTests`,
  `-testPlatform`, `-testResults` are stable across 2019.4 → 6000.x.
- NUnit3 result XML shape (`test-run` / `test-case` attributes) is what the
  Unity Test Framework has emitted since 2018; the parser is tolerant of
  missing attributes.
