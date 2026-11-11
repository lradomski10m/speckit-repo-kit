"""Check the host tools this repository needs, from the `prerequisites` section of repo-kit.json.

  python3 tools/prerequisites.py                     the core group (always checked)
  python3 tools/prerequisites.py --group native      core plus another group (repeatable)
  python3 tools/prerequisites.py --group all         every group
  python3 tools/prerequisites.py --list              the groups and their tools, checking nothing

On Windows run it as `python tools/prerequisites.py` or `py -3 tools/prerequisites.py`.

Installed and kept current by speckit-repo-kit's bootstrap.sh; change repo-kit.json, not this
file. Standard library only (Python 3.9+), so it runs before the repository's own environment
exists. It finds each tool (an environment variable naming it, its name and aliases on PATH, then
the candidate paths listed for this platform) and runs its version command; it installs nothing,
changes no account and uses no network. It exits nonzero if any checked tool is missing or too old.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "repo-kit.json"
VERSION = re.compile(r"(\d+(?:\.\d+)+)")
PLATFORMS = ("mac", "linux", "windows")


def platform_of(name: str = sys.platform) -> str:
    """The install-command key for a sys.platform value: mac, windows or linux."""
    if name == "darwin":
        return "mac"
    if name in ("win32", "cygwin", "msys"):
        return "windows"
    return "linux"


PLATFORM = platform_of()

# What the kit itself needs in every repository: Spec Kit, git worktrees, finalize's CI and pull
# request steps (gh) and this checker (python3). A repository entry of the same name replaces one.
SPECIFY_INSTALL = "uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@v1.1.1"
KIT_CORE = [
    {"name": "python3", "min": "3.9", "aliases": ["python", "py"],
     "mac": "brew install python", "linux": "sudo apt install python3",
     "windows": "winget install --id Python.Python.3.12 -e"},
    {"name": "git", "mac": "brew install git", "linux": "sudo apt install git",
     "windows": "winget install --id Git.Git -e"},
    {"name": "gh", "mac": "brew install gh", "linux": "sudo apt install gh",
     "windows": "winget install --id GitHub.cli -e",
     "then": "gh auth login --git-protocol https --web"},
    {"name": "specify", "min": "1.1.0", "args": ["version"],
     "mac": SPECIFY_INSTALL, "linux": SPECIFY_INSTALL, "windows": SPECIFY_INSTALL},
]


def groups(config: dict) -> dict[str, dict]:
    declared = config.get("prerequisites", {}).get("groups", {})
    core = dict(declared.get("core", {}))
    own = {tool["name"] for tool in core.get("tools", [])}
    core["tools"] = [t for t in KIT_CORE if t["name"] not in own] + core.get("tools", [])
    core.setdefault("description", "Build, test and run the Spec Kit flow")
    return {"core": core, **{k: v for k, v in declared.items() if k != "core"}}


def as_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def expand(pattern: str) -> str:
    """~ and environment variables ($VAR, ${VAR}, and %VAR% on Windows) in a candidate path."""
    return os.path.expandvars(os.path.expanduser(pattern))


def runnable(path: str) -> bool:
    return os.path.isfile(path) and (PLATFORM == "windows" or os.access(path, os.X_OK))


def from_paths(patterns: list[str]) -> list[str]:
    """The files the candidate paths name, in order; a glob gives its last match (the newest
    version, for names that carry one)."""
    found = []
    for pattern in patterns:
        matches = sorted(m for m in glob.glob(expand(pattern)) if runnable(m))
        if matches:
            found.append(matches[-1])
    return found


def candidates(tool: dict) -> tuple[list[tuple[str, bool]], str]:
    """Where to look for the tool, in order: (executable, whether it was found plainly on PATH).

    The second value is an error when the tool's environment variable is set but names nothing
    that runs: a variable set on purpose wins over everything else, so it isn't silently skipped.
    """
    variable = tool.get("env")
    value = os.environ.get(variable, "") if variable else ""
    if value:
        path = os.path.abspath(expand(value)) if os.sep in value or "/" in value else expand(value)
        if runnable(path):
            return [(path, False)], ""
        found = shutil.which(path)
        if found:
            return [(found, False)], ""
        return [], f"{variable}={value} is not a program that can run"
    found = []
    for index, name in enumerate([tool["name"], *tool.get("aliases", [])]):
        path = shutil.which(name)
        if path:
            found.append((path, index == 0))
    found += [(path, False) for path in from_paths(tool.get("paths", {}).get(PLATFORM, []))]
    unique = {}
    for path, plain in found:
        unique.setdefault(os.path.normcase(os.path.abspath(path)), (path, plain))
    return list(unique.values()), ""


def run_version(path: str, tool: dict) -> tuple[bool, str]:
    try:
        run = subprocess.run([path, *tool.get("args", ["--version"])], capture_output=True,
                             text=True, errors="replace", timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"does not run: {error}"
    found = VERSION.search(run.stdout + run.stderr)
    version = found.group(1) if found else "?"
    minimum = tool.get("min")
    if minimum and (not found or as_tuple(version) < as_tuple(minimum)):
        return False, f"{version}, need {minimum} or newer"
    if not found and run.returncode != 0:
        return False, f"does not run: exit {run.returncode}"
    return True, version


def check(tool: dict) -> tuple[bool, str]:
    """Return whether the tool is usable and a one-line report.

    Each place it's found is tried in turn and the first that runs and is new enough wins (on
    Windows, `python3` can be the Store's stand-in that only offers to install Python). The report
    names the executable when it isn't the tool's own name on PATH.
    """
    found, error = candidates(tool)
    if error:
        return False, error
    if not found:
        return False, "missing"
    first = None
    for path, plain in found:
        ok, report = run_version(path, tool)
        report = report if plain else f"{report} ({path})"
        if ok:
            return True, report
        first = first or report
    return False, first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--group", action="append", default=[], help="another group, or all")
    parser.add_argument("--list", action="store_true", help="list groups and tools, check nothing")
    args = parser.parse_args()

    config = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    known = groups(config)
    doc = config.get("prerequisites", {}).get("doc")
    if args.list:
        for name, group in known.items():
            tools = ", ".join(t["name"] for t in group.get("tools", []))
            print(f"{name:<12} {group.get('description', '')}\n{'':<12} {tools}")
        return 0
    wanted = list(known) if "all" in args.group else ["core", *args.group]
    unknown = [g for g in wanted if g not in known]
    if unknown:
        parser.error(f"unknown group {', '.join(unknown)}; groups: {', '.join(known)}")

    failed = 0
    width = max(len(t["name"]) for g in wanted for t in known[g].get("tools", [])) + 2
    for name in dict.fromkeys(wanted):
        print(f"[{name}] {known[name].get('description', '')}")
        for tool in known[name].get("tools", []):
            ok, report = check(tool)
            print(f"  {'ok ' if ok else 'NO '} {tool['name']:<{width}}{report}")
            if not ok:
                failed += 1
                if tool.get(PLATFORM):
                    print(f"       install: {tool[PLATFORM]}")
                if tool.get("then"):
                    print(f"       then:    {tool['then']}")
    if failed:
        print(f"\n{failed} tool(s) need attention." + (f" More in {doc}." if doc else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
