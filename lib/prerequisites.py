"""Check the host tools this repository needs, from the `prerequisites` section of repo-kit.json.

  python3 tools/prerequisites.py                     the core group (always checked)
  python3 tools/prerequisites.py --group native      core plus another group (repeatable)
  python3 tools/prerequisites.py --group all         every group
  python3 tools/prerequisites.py --list              the groups and their tools, checking nothing

Installed and kept current by speckit-repo-kit's bootstrap.sh; change repo-kit.json, not this
file. Standard library only (Python 3.9+), so it runs before the repository's own environment
exists. It reads PATH and runs each tool's version command; it installs nothing, changes no
account and uses no network. It exits nonzero if any checked tool is missing or too old.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "repo-kit.json"
VERSION = re.compile(r"(\d+(?:\.\d+)+)")
PLATFORM = "mac" if sys.platform == "darwin" else "linux"

# What the kit itself needs in every repository: Spec Kit, git worktrees, finalize's CI and pull
# request steps (gh) and this checker (python3). A repository entry of the same name replaces one.
KIT_CORE = [
    {"name": "python3", "min": "3.9", "mac": "brew install python", "linux": "sudo apt install python3"},
    {"name": "git", "mac": "brew install git", "linux": "sudo apt install git"},
    {"name": "gh", "mac": "brew install gh", "linux": "sudo apt install gh",
     "then": "gh auth login --git-protocol https --web"},
    {"name": "specify", "min": "1.1.0", "args": ["version"],
     "mac": "uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@v1.1.1",
     "linux": "uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@v1.1.1"},
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


def check(tool: dict) -> tuple[bool, str]:
    """Return whether the tool is usable and a one-line report."""
    name = tool["name"]
    if not shutil.which(name):
        return False, "missing"
    try:
        run = subprocess.run([name, *tool.get("args", ["--version"])], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"does not run: {error}"
    found = VERSION.search(run.stdout + run.stderr)
    version = found.group(1) if found else "?"
    minimum = tool.get("min")
    if minimum and (not found or as_tuple(version) < as_tuple(minimum)):
        return False, f"{version}, need {minimum} or newer"
    return True, version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--group", action="append", default=[], help="another group, or all")
    parser.add_argument("--list", action="store_true", help="list groups and tools, check nothing")
    args = parser.parse_args()

    config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
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
