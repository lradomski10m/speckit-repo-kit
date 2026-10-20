# Design

What is true about the system now, one doc per area (a component, a flow, an interface or a
cross-cutting concern). Docs here are living: a change updates the docs of the areas it touches in
the same pull request.

- **Grows with the work.** No doc is written up front. A feature that builds or reshapes an area
  with no doc creates one from [TEMPLATE.md](TEMPLATE.md) (its plan says so under "Design
  context"; `/speckit-finalize-run` step `design-sync` does it).
- **Links point from living to frozen.** A design doc links to the specs and ADRs behind it; specs
  and ADRs never link here.
- When a spec and a design doc disagree, the design doc states what is true now.

## Docs

| Doc | Area | Status |
| --- | --- | --- |
