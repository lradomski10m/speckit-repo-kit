# Specs

A spec is the frozen record of one change: what it set out to do and how (`spec.md`, `plan.md`,
`tasks.md` and their companions, produced by Spec Kit). What is true now lives in
[`docs/design/`](../docs/design/README.md), not here.

| Folder | Status |
| --- | --- |
| `specs/<name>/` | In progress |
| `specs/done/<name>/` | Done: the change landed |
| `specs/superseded/<name>/` | Replaced, retired or withdrawn |

- **Name**: `specs/YYYY-MM-DD-<kebab-name>/`, chosen by the `dated-specs` hook when
  `/speckit-specify` runs.
- **Frozen after merge** except its `**Status**` line and folder; a later change gets a new spec.
  `/speckit-finalize-run` (step `archive`) moves a finished spec inside the feature's own pull
  request, so it reaches `specs/done/` exactly when the feature merges.
- A checked task is evidence the work was done once, not that it still exists.

## In progress

## Done

## Superseded
