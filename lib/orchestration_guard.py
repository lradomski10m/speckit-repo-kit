#!/usr/bin/env python3
"""Claude Code PreToolUse hook (matcher: Agent): enforce the repo kit's orchestration rule.

The agent running the prompt plans and supervises; subagents do the work. Every subagent call
must name its model (opus, sonnet or haiku, chosen for the task) and its reasoning effort
(medium unless the task needs more). A call without them is denied with the reason, so the agent
corrects it and retries. Standard library only. Installed as tools/orchestration_guard.py.
"""
import json
import sys

MODELS = {"opus", "sonnet", "haiku"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def problems(tool_input: dict) -> list[str]:
    found = []
    if tool_input.get("subagent_type") == "fork":
        found.append("a fork always inherits the supervisor's model; start a fresh agent instead")
    model = tool_input.get("model")
    if model not in MODELS:
        found.append(f"set model to one of {sorted(MODELS)}, chosen for the task "
                     "(haiku: lookups and mechanical edits; sonnet: most implementation and tests; "
                     "opus: hard design, debugging and review)")
    effort = tool_input.get("effort")
    if effort not in EFFORTS:
        found.append("set effort ('medium' by default; higher only when the task needs it)")
    return found


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return  # not a hook call: never block on bad input
    if event.get("tool_name") != "Agent":
        return
    found = problems(event.get("tool_input") or {})
    if found:
        reason = ("Orchestration rule (AGENTS.md): delegate with an explicit model and effort. "
                  + "; ".join(found) + ".")
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))


if __name__ == "__main__":
    main()
