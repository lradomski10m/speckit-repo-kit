"""Antigravity PreToolUse hook written by speckit-repo-kit: approve what repo-kit.json grants.

Installed as .agents/repo-kit/approve.py and run from .agents/hooks.json, which routes only the
tools the config grants to it, with the group as the one argument:

  files     file tools (read, write, edit, list, search): approved when every path they name is
            in this repository
  commands  run_command: approved (repo-kit.json permissions.commands is "all")
  network   web and browser tools: approved (repo-kit.json permissions.network is "full")

It reads the tool call as JSON on stdin and prints {"decision": ...}: "auto_approve" where it
grants, otherwise "allow", which leaves the call to Antigravity's own rules and prompts. Your own
deny and ask rules still win over "auto_approve". Standard library only; written by the kit, so
change repo-kit.json and rerun the kit's bootstrap.sh rather than editing this file.
"""

import json
import os
import sys

# This file lives at <repo>/.agents/repo-kit/approve.py.
ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# The argument names file tools give their paths in. The products name their tools differently
# (the CLI's write_file takes Path, the documented write_to_file takes TargetFile), so a call is
# judged by its paths, not by its name: every path it names must be in this repository.
PATH_ARGS = ("Path", "AbsolutePath", "TargetFile", "DirectoryPath", "SearchDirectory", "SearchPath",
             "FilePath", "File", "path", "file_path")


def inside(path: str) -> bool:
    full = os.path.realpath(os.path.join(ROOT, os.path.expanduser(path)))
    return full == ROOT or full.startswith(ROOT + os.sep)


def decide(group: str, call: dict) -> str:
    name = call.get("name", "")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    if group == "files":
        paths = [args[key] for key in PATH_ARGS if key in args]
        ok = paths and all(isinstance(p, str) and p and inside(p) for p in paths)
        return "auto_approve" if ok else "allow"
    if group == "commands":
        return "auto_approve" if name == "run_command" else "allow"
    if group == "network":
        web = name in ("read_url_content", "search_web", "web_search") or name.startswith("browser_")
        return "auto_approve" if web else "allow"
    return "allow"


def main() -> None:
    group = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
        call = payload.get("toolCall") or {}
        decision = decide(group, call) if isinstance(call, dict) else "allow"
    except (ValueError, AttributeError):
        decision = "allow"
    print(json.dumps({"decision": decision}))


if __name__ == "__main__":
    main()
