---
description: "Finalize a feature: converge, design sync, verify, test, review, archive, push, branch CI, pull request and PR checks, ending with a green PR for people to review and merge; after the merge, offer worktree cleanup"
---

# Finalize the feature

Drive the current feature from "implemented" to "a green pull request ready for review", using the
steps in `.specify/extensions/finalize/finalize-config.yml`, in order. The list is the contract:
never skip, reorder or add a step that is not in it. Finalization never merges the PR, arms
auto-merge or queues it; people review and merge. After the merge, `--from cleanup` offers to
remove the feature's worktrees.

## User Input

```text
$ARGUMENTS
```

Arguments: `--from <id>` and `--to <id>` bound the steps run (inclusive; default: first and last).
An explicit bound completes only that segment, not the feature. After review comments, rerun with
`--from verify`. The archive step moves the spec to `specs/done/<name>/`: when resuming, resolve
the feature from its moved path (`SPECIFY_FEATURE_DIRECTORY=specs/done/<name>`). Removing worktrees
needs its own explicit approval of the listed paths; approval to ship does not cover it.

## Setup

1. Resolve the feature directory: `.specify/scripts/bash/check-prerequisites.sh --json --paths-only`
   (`FEATURE_DIR`, `FEATURE_SPEC`). Stop if it fails.
2. Read `.specify/extensions/finalize/finalize-config.yml`. If a step lacks `id` or `run`, or
   `--from`/`--to` names no step, stop and say which.
3. Read `.specify/memory/constitution.md`, the feature's `spec.md`, `plan.md` and `tasks.md`, and
   `AGENTS.md`; load the repository's pull-request/CI skill if `AGENTS.md` names one. Keep the
   pushed commit and the PR number across retries.

## Running a step

For each step from `--from` to `--to`:

- `run: /speckit-<name>`: run that Spec Kit command as you would if the user had typed it.
- `run: skill:<name>`: load that skill and do what the step's `description` says with it. For
  `skill:speckit-finalize-run` (this command), do the work the description lists yourself.
  The review step uses `canon-review`, installed by the kit with `canon-pr-review` and
  `canon-design-review` beside it. It reviews the local diff before any PR exists, so follow its
  practices and verdict rules but take the diff from git and render the result instead of posting
  a PR comment.
- `run: $ <command>`: run the shell command from the repository root.
- `run: gate`: stop, and tell the person what to review and how to continue.
- `until: <condition>`: repeat the step until the condition holds (`no-new-tasks`, `green`);
  judge the description, not just a command's exit status.

A step passes only when its `description` holds. A failing test or check, or a conflict, enters
the repair loop below; it is not a handoff to the user.

## Repair and quiet waiting

1. Diagnose the exact failing check or test on the commit that was tested. Read only the failing
   job's details or log.
2. Fix the cause, run the relevant local validation, and repeat from `verify` through `push`.
   Revisit converge or design-sync when the fix changes requirements or design. A new commit
   invalidates earlier CI results. Keep looping until branch CI and the PR's checks are green on
   the current commit.
3. Wait with `python3 .specify/extensions/finalize/scripts/checks.py branch|pr`. It polls GitHub
   quietly inside one process and prints one JSON result (exit 0 green, 1 something to act on,
   2 deadline reached). Await that one process; do not start another watcher or poll GitHub
   yourself turn by turn. Keep progress updates brief and fetch no logs when checks are green.
4. Pending checks mean wait; never start or restart builds by hand. A deadline (exit 2) means
   incomplete: check that CI is healthy, then wait again.

Stop only when missing authority, credentials or access, an unavailable external service, or an
unresolved product decision prevents further safe progress after the available recovery. Ordinary
failures, conflicts, pending checks and a waiting deadline are not blockers. Do not waive checks or
expand into unrelated infrastructure repairs. Report a real blocker with its evidence, the recovery
tried, the external action required and the step to resume from.

## Archive and worktree cleanup

The spec is archived inside the feature PR, before it is pushed: `git mv specs/<name>
specs/done/<name>`, **Status** Done, its `specs/README.md` row under Done, links to the old path
updated. It reaches `specs/done/` on the base branch exactly when the feature merges; if the PR
is closed instead, nothing moves. No separate archival PR.

`cleanup` runs only after GitHub reports the PR merged. List the feature's worktree paths and ask
for explicit approval of those paths before deleting any. A decline or no answer keeps them and
does not undo completion. Inspect each approved worktree for uncommitted or untracked work, ignored
files that exist nowhere else and running processes; keep anything unsafe to remove and say why.
Remove approved, safe worktrees with `git worktree remove` from a checkout that stays, without
force. Leave local branches intact. Never run an automatic sweep or broad cleanup that could
remove worktrees nobody approved.

## Rules

- Follow `AGENTS.md`. Push only the feature branch, never `main`, never force; never start CI
  builds by hand; never add a waiver (skip, ignore comment, loosened bound) to pass `verify` or
  `test`.
- Never merge the PR, arm auto-merge or add it to a merge queue.
- Commit messages name the issue the change serves (`Refs #N` or `Closes #N`) from the first commit.
- A review finding or failed check is owned through diagnosis, repair, validation and pushing again.

## Report

Report the completed steps, the PR URL, the commit that was tested and its branch and PR check
results, and the archived spec path. Say plainly whether the PR is green and ready for review, or
which step stopped and why. After cleanup, say whether worktree removal was approved, declined,
pending or done, and list any worktree kept and why.
