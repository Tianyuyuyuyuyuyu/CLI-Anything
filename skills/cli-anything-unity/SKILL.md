---
name: "cli-anything-unity"
description: >-
  Agent-native CLI for Unity Editor: project creation/inspection, batch-mode
  editor script execution, Unity Test Framework runs, player builds, and log
  inspection with JSON output and an interactive REPL.
---

# Unity Editor CLI

`cli-anything-unity` wraps the Unity Editor's official command-line interface
(`Unity.exe -batchmode ...`) so agents can drive real Unity projects: create
projects, inspect packages/scenes/assets, run compiled editor methods, run
tests, build players, and tail editor logs.

## Installation

```bash
pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=unity/agent-harness
```

## Requirements

- Unity Editor 2019.4+ (tested with 2021.3). The binary does **not** need to
  be on PATH if you set the `UNITY_BIN` environment variable, e.g.
  `UNITY_BIN="C:\Program Files\Unity\Hub\Editor\2021.3.8f1\Editor\Unity.exe"`.
  Detection order: `--editor` flag > `UNITY_BIN` > PATH > Unity Hub install
  dirs > Windows registry.
- An activated Unity license (Personal works). Batch mode fails without one.
- `-executeMethod` targets must be **static void methods in compiled Editor
  scripts** inside the project (or a package).

## Commands

### Engine

```bash
cli-anything-unity engine info              # locate binary + version
cli-anything-unity --editor "path/to/Unity.exe" engine info
cli-anything-unity engine version
cli-anything-unity engine return-license    # release the license seat (-returnlicense)
```

### Project Management

```bash
cli-anything-unity project create ./MyProject        # -batchmode -createProject
cli-anything-unity project info ./MyProject          # version, packages, scenes, assets
cli-anything-unity project packages ./MyProject      # manifest.json dependencies
cli-anything-unity project open ./MyProject          # launch editor GUI (non-blocking)
cli-anything-unity project scaffold ./MyProject      # write Assets/Editor/CliBuild.cs
cli-anything-unity project import ./MyProject tool.unitypackage        # -importPackage
cli-anything-unity project export ./MyProject out.unitypackage Assets/Folder  # -exportPackage
```

### Editor Script Execution

```bash
cli-anything-unity execute ./MyProject MyApp.Editor.MyTool.Run
cli-anything-unity execute ./MyProject CliBuild.Perform --log-file build.log
```

### Prefabs

```bash
cli-anything-unity prefab list ./MyProject                       # all *.prefab assets
cli-anything-unity prefab info ./MyProject Assets/Prefabs/Hero.prefab
cli-anything-unity prefab scaffold ./MyProject                   # write Assets/Editor/CliPrefabTools.cs

# create an empty prefab (root named after the asset unless --name given)
cli-anything-unity prefab create ./MyProject Assets/Prefabs/Hero.prefab --name Hero

# save an existing scene GameObject (hierarchy path) as a prefab
cli-anything-unity prefab create-from-scene ./MyProject \
    Assets/Scenes/SampleScene.unity "Parent/Child" Assets/Prefabs/Child.prefab

# edit an existing prefab
cli-anything-unity prefab edit ./MyProject Assets/Prefabs/Hero.prefab \
    --rename Boss --set-active false --position 1,2,3
```

`prefab info` parses the serialized YAML directly (fast, no editor launch).
`prefab create` / `create-from-scene` / `edit` run the real editor via
`PrefabUtility` (requires `prefab scaffold` once; re-run scaffold with
`--force` after upgrading the CLI to get new entry methods).

### Testing

```bash
cli-anything-unity test ./MyProject --platform EditMode           # or PlayMode
cli-anything-unity test ./MyProject --filter Tests.Foo.Bar        # -testFilter
cli-anything-unity test ./MyProject --category Smoke --sync       # -testCategory -runSynchronously
```

### Raw flag passthrough

Everything after `--` is forwarded verbatim to Unity, so any editor flag
not wrapped above is still reachable:

```bash
cli-anything-unity execute ./MyProject My.App.Run -- -accept-apiupdate -disable-assembly-updater
```

Requires `com.unity.test-framework` in the project. Writes NUnit XML results
and returns a summary (total/passed/failed/skipped + failure messages).

### Player Builds

```bash
cli-anything-unity project scaffold ./MyProject      # once, generates CliBuild.cs
cli-anything-unity build ./MyProject --target StandaloneWindows64 --output Builds/Win64
```

Targets: `StandaloneWindows64`, `StandaloneLinux64`, `StandaloneOSX`,
`Android`, `iOS`, `WebGL`, ... (custom targets allowed via `--method`).

### Logs

```bash
cli-anything-unity logs --lines 100                  # tail Editor.log
cli-anything-unity logs --file ./MyProject/build.log --grep "error"
```

## JSON Mode

Add `--json` (before the subcommand) for machine-readable output. Every
command returns `{"status": "ok" | "error", "action": ..., ...}`; errors
exit non-zero and include a `message` field.

## Interactive REPL

```bash
cli-anything-unity session
```

## For AI Agents

- Always add `--json` and check the `status` / exit code.
- Batch runs are slow (license check + domain reload each launch): use
  generous `--timeout` (default 900s) and prefer one `execute` call that
  does many editor operations over many small calls.
- Unity batch mode requires an activated license; `returncode` 1 with a
  "license" message in `message` means activation is needed.
- `execute`/`build` cannot run arbitrary C# strings: scaffold or add an
  Editor script first, then reference its static method.
- Unity license activation (`-serial/-username/-password`) is deliberately
  not wrapped: it involves credentials. Use the `--` passthrough if you
  must, and prefer `engine return-license` to free the seat afterwards.
