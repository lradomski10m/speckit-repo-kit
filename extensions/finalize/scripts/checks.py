"""Wait quietly for CI on a pushed branch or on a pull request.

  python3 .specify/extensions/finalize/scripts/checks.py branch [BRANCH] [options]
  python3 .specify/extensions/finalize/scripts/checks.py pr [PR] [options]
  options: --repo OWNER/NAME  --interval 60  --timeout 7200  --no-checks-after 600

- branch: the CI that runs on push, judged on the branch's newest commit on GitHub (default: the
  current branch). If no check has reported on that commit after --no-checks-after seconds, the
  result is `no-checks`: the repository runs no CI on branch pushes.
- pr: the PR's checks on its head commit (default: the current branch's PR), including each check
  the base branch requires. A conflict, a closed PR or a draft is reported at once.

Polls GitHub through the saved `gh` login inside one process and prints one JSON line at the end,
never build logs or a line per poll. Exit 0: every check passed. Exit 1: something to act on
(a failed check, a conflict, a closed PR, no CI on the branch). Exit 2: still running at the
deadline; if nothing is wrong, wait again rather than restarting builds. An already merged PR
exits 0. Read-only: it never
pushes, merges or starts a build. Standard library only (Python 3.9+), plus the GitHub CLI.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import quote

PASSED = {"success", "neutral", "skipped"}
FAILED = {"failure", "error", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}


@dataclass
class Result:
    state: str
    target: str
    url: str = ""
    commit: str = ""
    checks: dict = field(default_factory=dict)
    links: dict = field(default_factory=dict)
    reason: str = ""


# Branch protection that cannot be read: not configured (404), or a private repository on a plan
# without the feature (403 "Upgrade to GitHub Pro or make this repository public ...").
NO_PROTECTION = r"HTTP 404|Not Found|HTTP 403.*(upgrade|not available)|(upgrade|not available).*HTTP 403"


def gh(*args: str, allow_missing: bool = False, missing: str = r"HTTP 404|Not Found"):
    """Run gh and parse its JSON; retry what looks transient, raise on anything else."""
    for attempt in range(4):
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
        if done.returncode == 0:
            return json.loads(done.stdout) if done.stdout.strip() else None
        detail = (done.stderr or done.stdout).strip()
        if allow_missing and re.search(missing, detail, re.I | re.S):
            return None
        if attempt < 3 and re.search(r"HTTP 5\d\d|timeout|connection reset|TLS", detail, re.I):
            time.sleep(5 * (attempt + 1))
            continue
        raise RuntimeError(f"gh {' '.join(args)}: {detail}")


def check_states(repo: str, sha: str) -> tuple[dict, dict]:
    """Newest state of every check run and commit status on one commit."""
    states, links = {}, {}
    runs = gh("api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100")["check_runs"]
    for run in sorted(runs, key=lambda r: (r.get("started_at") or "", r["id"])):
        states[run["name"]] = run["conclusion"] if run["status"] == "completed" else "pending"
        if run.get("details_url"):
            links[run["name"]] = run["details_url"]
    for status in reversed(gh("api", f"repos/{repo}/commits/{sha}/statuses?per_page=100")):
        states[status["context"]] = status["state"]
        if status.get("target_url"):
            links[status["context"]] = status["target_url"]
    return states, links


def required_checks(repo: str, branch: str) -> set:
    found = gh("api", f"repos/{repo}/branches/{branch}/protection/required_status_checks",
               allow_missing=True, missing=NO_PROTECTION)
    if not found:
        # No protection, or the plan does not offer it: judge every check reported on the PR head.
        print("note: no required checks known for " + branch + "; judging every reported check",
              file=sys.stderr)
        return set()
    return set(found.get("contexts") or []) | {c["context"] for c in found.get("checks") or []}


def judge(result: Result, required: set = frozenset()) -> Result:
    states = result.checks
    if any(state in FAILED for state in states.values()):
        failed = sorted(name for name, state in states.items() if state in FAILED)
        result.state, result.reason = "failed", "failed: " + ", ".join(failed)
    elif states and not required - set(states) and all(s in PASSED for s in states.values()):
        result.state = "green"
    else:
        missing = sorted(required - set(states))
        result.reason = "waiting for " + (", ".join(missing) if missing else "checks to finish")
    return result


def poll_branch(repo: str, branch: str, started: float, no_checks_after: float) -> Result:
    found = gh("api", f"repos/{repo}/branches/{quote(branch, safe='')}", allow_missing=True)
    if not found:
        return Result("not-pushed", branch, reason=f"{branch} is not on GitHub: push it first")
    commit = found["commit"]
    result = Result("waiting", branch, url=commit["html_url"], commit=commit["sha"])
    result.checks, result.links = check_states(repo, commit["sha"])
    if not result.checks:
        if time.monotonic() - started > no_checks_after:
            result.state = "no-checks"
            result.reason = "no CI reported on this commit: the repository runs no CI on branch pushes"
        else:
            result.reason = "waiting for CI to start"
        return result
    return judge(result)


def poll_pr(repo: str, number: int) -> Result:
    pull = gh("api", f"repos/{repo}/pulls/{number}")
    result = Result("waiting", f"#{number}", url=pull["html_url"], commit=pull["head"]["sha"])
    if pull.get("merged"):
        result.state, result.reason = "merged", "the PR is already merged"
    elif pull["state"] == "closed":
        result.state, result.reason = "closed", "the PR was closed without merging"
    elif pull.get("mergeable") is False or pull.get("mergeable_state") == "dirty":
        result.state, result.reason = "conflict", "merge the base branch, resolve conflicts, push again"
    else:
        result.checks, result.links = check_states(repo, result.commit)
        judge(result, required_checks(repo, pull["base"]["ref"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=("branch", "pr"))
    parser.add_argument("target", nargs="?", help="branch name or PR number; default: the current branch's")
    parser.add_argument("--repo", help="OWNER/NAME; default: the current directory's repository")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--timeout", type=float, default=7200)
    parser.add_argument("--no-checks-after", type=float, default=600)
    args = parser.parse_args()
    if min(args.interval, args.timeout, args.no_checks_after) <= 0:
        parser.error("--interval, --timeout and --no-checks-after must be positive")
    repo = args.repo or gh("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
    branch = subprocess.run(
        ["git", "branch", "--show-current"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if args.what == "branch":
        target = args.target or branch
    else:
        # With --repo, gh needs the branch named explicitly to find its pull request.
        target = args.target or str(gh("pr", "view", branch, "--json", "number", "-R", repo)["number"])

    def poll() -> Result:
        if args.what == "branch":
            return poll_branch(repo, target, started, args.no_checks_after)
        return poll_pr(repo, int(target))

    started = time.monotonic()
    while True:
        result = poll()
        if result.state != "waiting":
            break
        remaining = started + args.timeout - time.monotonic()
        if remaining <= 0:
            result.state = "timeout"
            result.reason = f"deadline reached ({result.reason}); wait again if nothing is wrong, never restart builds"
            break
        time.sleep(min(args.interval, remaining))
    print(json.dumps(asdict(result)))
    return 0 if result.state in {"green", "merged"} else 2 if result.state == "timeout" else 1


if __name__ == "__main__":
    sys.exit(main())
