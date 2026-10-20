# Spec Kit repo kit

Sets up [Spec Kit](https://github.com/github/spec-kit) in any repository the way servo uses it:
dated spec folders, plans that name the design docs and decisions they touch, task lists that
always end with the records work, and one finishing command that takes a feature to merge.

```bash
./bootstrap.sh <repo-path>                  # reads <repo-path>/repo-kit.json if there is one
./bootstrap.sh --network off <repo-path>    # decide network access without being asked
./bootstrap.sh --refresh <repo-path>        # reinstall the kit's components after a kit change
```

Running it again is safe: anything already installed is skipped, and existing files are never
overwritten.

Needs: `git`, `bash`, `python3` (3.9+, standard library only) and the Spec Kit CLI 1.1.0 or
newer (`specify`). Finalize's CI and pull-request steps use the GitHub CLI (`gh`).

## The config file

Each repository commits a `repo-kit.json` at its root. Start from
[repo-kit.example.json](repo-kit.example.json).

| Key | Meaning | Default |
| --- | --- | --- |
| `agents` | Agents to set up: `claude`, `codex`, `agy` (Antigravity). The first is Spec Kit's default | `["claude", "codex"]` |
| `permissions.network` | `full` or `off`. If it isn't set, the script asks the person running it (or takes `--network`) and writes the answer here; it never grants network access silently | asked |
| `skills` | Skills from elsewhere: `name`, `source` (a git URL, or a path relative to this file), `ref` (tag, branch or commit; required for git), `path` (the folder inside the source) | none |
| `claude.marketplaces` | Claude Code plugin marketplaces, by name. Each value is a marketplace source, passed through unchanged | none |
| `claude.plugins` | Plugins to enable, as `<plugin>@<marketplace>` | none |

The script checks the whole file before changing anything.

### What it sets up from the file

**Skills.** Each skill is copied into `.agents/skills/<name>/`, where Codex and Antigravity read
it. `.claude/skills/<name>` is a link to that copy, never a second copy. `repo-kit.lock.json`
records the exact commit and a digest of each copy. A copy edited by hand is refused instead of
overwritten (change the skill where it comes from and bump `ref`). A skill removed from the config
is removed from the repository.

**Permissions**, written into committed files. Each person approves them once, when their agent
asks to trust the folder:

| | Claude Code (`.claude/settings.json`) | Codex (`.codex/config.toml`, a marked block) |
| --- | --- | --- |
| Write in the repo | `Edit(/**)` | `sandbox_mode = "workspace-write"`, `approval_policy = "on-request"` |
| Worktrees and commits | `Bash(git worktree *)` | `.git` writable (Codex keeps it read-only by default) |
| Network, if `full` | `WebFetch(domain:*)` (also opens shell-command network under Claude's sandbox), `WebSearch` | `network_access = true`, `web_search = "live"` |

Switching `network` to `off` removes those rules again. Rules the kit doesn't manage are left
alone.

**Worktrees** live in one standard place for both agents: `.claude/worktrees/<name>/`, which is
added to `.gitignore`. It's Claude Code's default (`--worktree`, subagent worktrees), it's inside
both agents' sandboxes, and it needs no machine-specific paths. Codex sessions start at the main
checkout. Codex blocks writes to the git data of the folder a session starts in, so a session
started inside a worktree can't commit.

**Agent context.** A marked "Agent setup" section in `AGENTS.md` tells every agent the rules
above: where skills live and not to edit external ones, the write and network rules, and how to
use worktrees. It's regenerated from the config on every run. A missing `CLAUDE.md` is created as
`@AGENTS.md`, so Claude Code reads the same file.

**Plugins.** The Claude entries go into `.claude/settings.json` (`extraKnownMarketplaces`,
`enabledPlugins`), and Claude Code offers to install them when the folder is trusted. A plugin only
adds to what an agent can do in a session. Anything CI or a reviewer depends on stays committed,
because CI and cloud sessions don't load plugins.

### What was checked, and what wasn't

- **Codex:** with these settings, `git worktree add` and a commit inside the worktree work, while
  the defaults block both (checked with `codex sandbox`, Codex 0.163). Network is blocked or
  allowed by `network_access`. A trusted folder's `.codex/config.toml` sets the network and
  approval policy (`codex doctor`). Not checked in a live Codex session: that the relative `.git`
  path is read correctly from the project file. `codex sandbox` doesn't load project files.
- **Claude Code:** the rules follow the permissions and sandbox documentation (Claude Code
  2.1.302). They weren't exercised in a live session.

## What it installs

| Piece | Kind | What it does |
| --- | --- | --- |
| `dated-specs` | Spec Kit extension | Before `/speckit-specify`, names the folder `specs/YYYY-MM-DD-<name>/` (numbers collide between parallel branches; dates don't) |
| `finalize` | Spec Kit extension | `/speckit-finalize-run`: converge → design sync → verify → test → review → push → branch CI → open the PR → PR checks, ending with a green PR handed to people to review and merge. It never merges. It owns repair: a failed check, test or conflict is diagnosed, fixed and pushed again. The steps are a list in `finalize-config.yml`; `scripts/checks.py branch\|pr` waits quietly for CI (one JSON result) |
| `sdd-records` | Spec Kit preset | Plan template: "Design context" (design docs read, updated or created) and "Significant decisions" (each one becomes an ADR). Tasks template: tests are required, and every list ends with design docs, agent guidance, ADRs, verify and finalize |
| `sdd` | Spec Kit workflow | `specify workflow run sdd`: specify → plan → tasks → implement → finalize, stopping for a person to approve the spec and the plan, and to review and merge the green PR |
| `canon-review`, `canon-pr-review`, `canon-design-review` | Skills from [npow/claude-skills](https://github.com/npow/claude-skills), pinned in `kit-skills.json` | Finalize's review step: a code and design review of the diff against engineering practices, with an APPROVE / REQUEST_CHANGES / BLOCK decision. A repository replaces one by listing a skill of the same name in its `repo-kit.json` |
| `scaffold/` | Files, copied only if missing | READMEs for `specs/`, `docs/design/` (with `TEMPLATE.md`), `docs/decisions/` and `docs/analysis/` |

Spec Kit writes each agent's own copy of the commands (`.claude/skills/`, and `.agents/skills/`
for both Codex and Antigravity), so a single kit serves all three.

## After bootstrapping

1. Write the constitution: `/speckit-constitution`. The plan template checks every plan against
   each of its principles.
2. Make sure `AGENTS.md` names the repository's `verify` and `test` commands. If they aren't
   `make verify` and `make test`, edit the shell steps in
   `.specify/extensions/finalize/finalize-config.yml`. That file is meant to be edited per repo.
3. Commit `.specify/`, `.claude/`, `.codex/`, `.agents/`, `AGENTS.md`, `CLAUDE.md`, `.gitignore`,
   `repo-kit.json`, `repo-kit.lock.json` and the record folders.

Design docs aren't written up front. Each one appears when a feature first builds or reshapes
that area: the plan names it under "Design context", and `finalize` (step `design-sync`) writes it
from `docs/design/TEMPLATE.md`.

## Why Spec Kit's own packaging

Extensions, presets and workflows are Spec Kit's native ways to extend it. They install with
`specify ... add --dev <folder>` and are rendered for every agent the repository uses, so nothing
here is specific to Claude or Codex. A Claude or Codex plugin would serve only one agent, and CI
couldn't see it.

A Spec Kit **bundle** (`bundle.yml`, one `specify bundle install` for everything) is the next step.
In Spec Kit 1.1.1, a bundle installs its extensions and presets only from a published catalog, not
from a local folder. Once the extensions and preset are published (zip releases plus a catalog
JSON), a bundle can replace `bootstrap.sh`.

## Changing the kit

- Change the files here, bump the component's `version`, then run `bootstrap.sh --refresh` in
  each repository.
- `finalize-config.yml` belongs to each repository once installed, and Spec Kit keeps it on
  reinstall. The kit updates it only while the repository hasn't edited it: `repo-kit.lock.json`
  records the version the kit last wrote. An edited copy is left alone, and the next run prints
  the `diff` command against the kit's new version, once per kit change.
- In a target repository, never edit the generated `speckit-*` skills by hand. Change the
  extension's command file here, or the repository's own `.specify/templates/overrides/`. Those
  overrides take precedence over this preset.
