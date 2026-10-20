---
description: "Choose the dated feature directory (specs/YYYY-MM-DD-<short-name>/) for the spec about to be created"
---

# Choose the dated feature directory

This repository names new specs `specs/YYYY-MM-DD-<kebab-name>/`: sequential numbers collide
when features are specified on parallel branches, dates do not. Spec Kit's own numbering offers only `NNN-` and
`YYYYMMDD-HHMMSS-`, so this hook chooses the directory and hands it to the specify command, which
uses an explicitly provided `SPECIFY_FEATURE_DIRECTORY` as-is.

## User Input

```text
$ARGUMENTS
```

## Steps

1. If the user already gave `SPECIFY_FEATURE_DIRECTORY`, keep it and stop; report it unchanged.
2. Generate the feature's short name exactly as the specify command describes (2-4 words,
   action-noun form, lowercase kebab-case).
3. Run from the repository root:

   ```bash
   .specify/extensions/dated-specs/scripts/bash/feature-directory.sh "<short-name>"
   ```

   It prints one line, `SPECIFY_FEATURE_DIRECTORY=specs/<today>-<short-name>` (with `-2`, `-3`
   appended if that folder already exists in `specs/`, `specs/done/` or `specs/superseded/`), and
   creates nothing.
4. Report that line. The specify command MUST use this value as the explicitly provided
   `SPECIFY_FEATURE_DIRECTORY` (resolution step 1), not the numbering from `init-options.json`.
