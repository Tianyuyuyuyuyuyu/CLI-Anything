# Unity CLI (cli-anything-unity)

Agent-native command line harness for the **Unity Editor**, built for the
[CLI-Anything](https://github.com/HKUDS/CLI-Anything) project.

It wraps the Unity Editor's official command-line interface
(`Unity.exe -batchmode ...`) so AI agents can create projects, run editor
scripts, execute the Unity Test Framework, build players, and read editor
logs — all with machine-readable `--json` output and an interactive REPL.

## Install

```bash
pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=unity/agent-harness
```

## Requirements

- Unity Editor 2019.4+ (tested against 2021.3). The editor does **not** need
  to be on PATH if you set the `UNITY_BIN` environment variable to the
  editor binary, e.g. `G:\UnitySoftware\Unity 2021.3.8f1\Editor\Unity.exe`.
- An activated Unity license (Personal is enough); batch mode fails without one.

## Quick start

```bash
cli-anything-unity engine info
cli-anything-unity project info ./MyProject
cli-anything-unity execute ./MyProject MyApp.Editor.MyTool.Run
cli-anything-unity test ./MyProject --platform EditMode
cli-anything-unity build ./MyProject --target StandaloneWindows64
```
