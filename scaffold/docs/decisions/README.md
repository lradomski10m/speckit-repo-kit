# Architecture decision records

One file per significant decision: hard to reverse, crossing areas, or binding later work. An ADR
says why X was chosen over Y, in the pull request that makes the decision. A plan lists such
decisions under "Significant decisions" and each becomes an ADR task.

- **Name**: `YYYY-MM-DD-<kebab-name>.md`.
- **Status line**: the third line, `**Status:** Proposed`, `Accepted` or
  `Superseded by <ADR link>`.
- **Frozen**: once accepted, an ADR changes only its status line. A change of mind is a new ADR
  that supersedes the old one.
- **Sections**: Context, Decision, Alternatives, Consequences.
