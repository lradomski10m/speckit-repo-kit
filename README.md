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

Needs: `git`, `bash`, Python 3.9+ (`python3`, `python` or `py -3`; standard library only) and the
Spec Kit CLI 1.1.0 or newer (`specify`). Finalize's CI and pull-request steps use the GitHub CLI
(`gh`). It runs on macOS and Linux, and on Windows under Git Bash ([Windows](#windows)).

## The config file

Each repository commits a `repo-kit.json` at its root. Start from
[repo-kit.example.json](repo-kit.example.json).

| Key | Meaning | Default |
| --- | --- | --- |
| `agents` | Agents to set up: `claude`, `codex`, `agy` (Antigravity). The first is Spec Kit's default | `["claude", "codex"]` |
| `permissions.network` | `full` or `off`. If it isn't set, the script asks the person running it (or takes `--network`) and writes the answer here; it never grants network access silently | asked |
| `permissions.commands` | `ask` or `all`. `all` lets Antigravity run every shell command in the repository without asking. Only Antigravity reads it; Claude Code and Codex keep the rules below | `ask` |
| `skills` | Skills from elsewhere: `name`, `source` (a git URL, or a path relative to this file), `ref` (tag, branch or commit; required for git), `path` (the folder inside the source) | none |
| `claude.marketplaces` | Claude Code plugin marketplaces, by name. Each value is a marketplace source, passed through unchanged | none |
| `claude.plugins` | Plugins to enable, as `<plugin>@<marketplace>` | none |
| `commands.verify`, `commands.test` | The repository's static-check and test commands, one shell line each. Finalize's verify and test steps run them, and `AGENTS.md` names them | `make verify`, `make test` |
| `prerequisites.groups` | Host tools by group: `<group>: {description, tools: [{name, min, args, env, aliases, paths, mac, linux, windows, then}]}`. `core` is always checked and always includes the kit's own tools (`python3`, `git`, `gh`, `specify`); a tool of the same name replaces the kit's. Only `name` is required; the rest are under [Tool entries](#tool-entries) | the kit's core tools |
| `prerequisites.doc` | A page for what a tool check can't show (accounts, sign-in), named when a check fails | none |

The script checks the whole file before changing anything.

### Tool entries

Each tool under `prerequisites.groups.<group>.tools`:

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string, required | The executable's name on `PATH` (`shutil.which`, so `.exe` and the rest of `PATHEXT` are found on Windows) |
| `min` | version string | The oldest version that will do. The version is the first `1.2[.3...]` in the output, so `4.7.2.stable.official.abc123` reads as `4.7.2` |
| `args` | list of strings | The version command's arguments (default `["--version"]`) |
| `env` | variable name | An environment variable naming the executable (a path, or a name on `PATH`), checked first: `"env": "GODOT"`. When it's set, it's the only place looked; set to something that doesn't run, the check fails and says so |
| `aliases` | list of strings | Other names to try on `PATH` after `name`, in order: `["python", "py"]` |
| `paths` | `{"mac": [...], "windows": [...], "linux": [...]}` | Where to look when it isn't on `PATH`, for each platform. Each entry may use `~`, environment variables (`$VAR`, `${VAR}`, and `%VAR%` on Windows) and glob patterns; of a pattern's matches the last in sorted order is taken (the newest, for names that carry a version) |
| `mac`, `linux`, `windows` | string | The install command shown on that platform when the tool is missing or too old (e.g. `brew install ...`, `sudo apt install ...`, `winget install --id ... -e`) |
| `then` | string | A step after installing, such as signing in |

Lookup order: `env`, then `name` and `aliases` on `PATH`, then `paths` for this platform. Each
place found is run in turn and the first that runs and is new enough is used, so Windows' `python3`
stand-in (which only offers to install Python from the Store) gives way to `python` or `py`. The
report names the executable when it isn't `name` on `PATH`: `ok godot 4.7.2
(/Applications/Godot.app/Contents/MacOS/Godot)`.

### What it sets up from the file

**Skills.** Each skill is copied into `.agents/skills/<name>/`, where Codex and Antigravity read
it. `.claude/skills/<name>` is a link to that copy, never a second copy. `repo-kit.lock.json`
records the exact commit and a digest of each copy. A copy edited by hand is refused instead of
overwritten (change the skill where it comes from and bump `ref`). A skill removed from the config
is removed from the repository.

**Permissions**, written into committed files. Claude Code and Codex apply theirs once each person
approves trusting the folder; Antigravity runs its workspace hook (below):

| | Claude Code (`.claude/settings.json`) | Codex (`.codex/config.toml`, a marked block) | Antigravity (`.agents/hooks.json`, the `repo-kit-permissions` hook) |
| --- | --- | --- | --- |
| Read and write in the repo | `Edit(/**)` | `sandbox_mode = "workspace-write"`, `approval_policy = "on-request"` | File tools approved when every path they name is in the repository; anything outside is left to Antigravity |
| Worktrees and commits | `Bash(git worktree *)` | `.git` writable (Codex keeps it read-only by default) | Shell commands, so only with `commands: all` |
| Every shell command, if `commands` is `all` | (not managed) | (not managed) | `run_command` approved |
| Network, if `full` | `WebFetch(domain:*)` (also opens shell-command network under Claude's sandbox), `WebSearch` | `network_access = true`, `web_search = "live"` | Web reading and search, and browser tools (`read_url_content`, `search_web`, `browser_*`) approved |

Switching `network` to `off` (or `commands` to `ask`) removes those grants again. Rules and hooks
the kit doesn't manage are left alone.

**Antigravity's permissions are a hook, because it has no permission file in the repository.**
Its rules (`allow`/`ask`/`deny` lists of `command(...)`, `write_file(...)`, `read_url(...)` and so
on, and `toolPermission`) live in each person's `~/.gemini/antigravity-cli/settings.json`; its
"Project" scope is kept in `~/.gemini/config/projects/`, and the app's per-project preset is set in
its settings screen. None of those can be committed. What a workspace can carry is
`.agents/hooks.json`, which the app, the CLI and the IDE all read, and a `PreToolUse` hook there may
answer `auto_approve`: approve a call the default policy would otherwise ask about, while the
person's own deny and ask rules still win. The kit owns the `repo-kit-permissions` entry in that
file and `.agents/repo-kit/`: `approve.py`, which decides, and `approve.sh`, which finds a Python
3.9+ (`python3`, `python`, then `py -3`) to run it. Commit all three. The hook's command is the same
line on every platform:

```
git -c "alias.repo-kit-approve=!sh .agents/repo-kit/approve.sh" repo-kit-approve <group>
```

Antigravity's documentation doesn't say which shell runs a hook command or where (the CLI runs
`sh -c` in `.agents/` on macOS; on Windows it may be cmd or PowerShell). This line has nothing those
shells read differently (no `$(...)`, `&&`, `||`, `;` or single quotes), and git runs an alias's
command with its own `sh` (Git for Windows ships one) at the top of the checkout it's started in, so
it works from any folder in the repository and a worktree uses its branch's copy. `git` and a
Python must be on `PATH`; with no Python the hook answers `allow` and Antigravity asks as usual.
Things the hook doesn't do:

- It doesn't turn on Antigravity's terminal sandbox or put domains in its network allowlist
  (`read_url` rules and `enableTerminalSandbox` are personal settings). With `commands: all`, an
  approved command can reach the network whatever `network` says, so treat `all` as the broad
  grant it is.
- It never approves a file outside the repository, a shell command with `commands: ask`, or a web
  or browser call with `network: off`; those prompt as Antigravity normally does.
- Antigravity runs a workspace's hooks without asking first: the CLI ran this one in a folder it
  had never been asked to trust. Review `.agents/hooks.json` in repositories you didn't write.

**Worktrees** live in one standard place for both agents: `.claude/worktrees/<name>/`, which is
added to `.gitignore`. It's Claude Code's default (`--worktree`, subagent worktrees), it's inside
both agents' sandboxes, and it needs no machine-specific paths. Codex sessions start at the main
checkout. Codex blocks writes to the git data of the folder a session starts in, so a session
started inside a worktree can't commit.

**Agent context.** A marked "Agent setup" section in `AGENTS.md` tells every agent the rules
above: where skills live and not to edit external ones, the write and network rules, and how to
use worktrees. It's regenerated from the config on every run. A missing `CLAUDE.md` is created as
`@AGENTS.md`, so Claude Code reads the same file.

**Checks and host tools.** `tools/prerequisites.py` (standard library only, so it runs before
any environment exists) checks the tools in `prerequisites`: `python3 tools/prerequisites.py`
(`python` or `py -3` on Windows),
`--group <name>` or `--group all`, `--list`. It prints the install command for each missing or
too-old tool and exits nonzero. It changes nothing and uses no network. The kit keeps the file
current like `finalize-config.yml`; the tool list lives in `repo-kit.json`. A repository usually
wraps it as `make prerequisites`.

**Antigravity** (`agy`) reads the same `.agents/skills/` folder as Codex, so it gets the
`speckit-*` commands and every skill, and its permissions come from the hook above.

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
- **Antigravity:** exercised with the CLI 1.5.4 in headless runs (`agy -p`), with the session's
  settings at `{"toolPermission": "request-review"}` (`--settings`, never saved). Without the
  hook, a shell command, a file write inside the folder and `read_url_content` were each refused
  ("headless mode cannot prompt"). With it: `commands: all` ran a command, a write and an edit in
  the repository went through and a write outside it was refused, and `network: full` fetched a
  page; with `network: off` and `commands: ask` the command and the fetch were refused again while
  repository writes still went through. The CLI's tool names differ from the documented ones
  (`write_file` with `Path`, not `write_to_file` with `TargetFile`), so the hook judges file calls
  by their path arguments and lists both names. Not checked: the Antigravity app and IDE (their
  documentation says they read `.agents/hooks.json` the same way), interactive sessions, browser
  tools, a person's deny or ask rule winning over the hook, and what Antigravity does when the
  hook fails (for example a worktree whose branch doesn't have the script yet). The git-alias form
  of the command was re-checked with the CLI 1.5.6 on macOS (`commands: all` ran a command and wrote
  a file; with `commands: ask` the command was refused and the write still went through), and by
  the tests from `.agents/`, a subfolder and a worktree under `sh`, `bash` and `zsh`. Not run on
  Windows (see below).

## Windows

Repositories set up by the kit are worked on from macOS and Windows alike. On Windows:

- **Run `bootstrap.sh` from Git Bash** (Git for Windows), with `make` and Python on `PATH`
  (`winget install --id Python.Python.3.12 -e`; Git Bash has no `make`, so install one, e.g.
  `winget install --id ezwinports.make -e`, if the repository's `verify`/`test` commands use it). The
  script finds Python as `python3`, `python` or `py -3`, passes Windows paths (`C:/...`) to it, and
  installs Spec Kit's `sh` scripts, which agents run through Git Bash, so a repository keeps one set
  of scripts for both systems. `chmod` changes nothing there; Git Bash runs a script by its `#!`
  line.
- **Line ends.** Git for Windows checks text out with CRLF by default, which breaks shell scripts.
  The kit adds `*.sh text eol=lf` to the repository's `.gitattributes` (and has the same line
  itself), writes every file with LF, and reads CRLF as LF when it compares digests, so a Windows
  checkout of a skill or `finalize-config.yml` isn't taken for a hand edit.
- **Symbolic links.** `.claude/skills/<name>` is a link to `.agents/skills/<name>`. Git checks a
  link out as a link only where symbolic links can be made: on Windows, turn on Developer Mode
  (Settings, System, For developers) and run `git config --global core.symlinks true` before
  cloning (or re-checkout afterwards). Without that, git writes each link as a small text file
  holding its target, and Claude Code doesn't find the skill. `bootstrap.sh` then replaces that file
  with a link if it can, and otherwise with a copy of the skill, saying so: don't commit the copy
  (it shows as a changed type in `git status`). The kit replaces its copy on each run and swaps it
  for a link once the machine can make one.
- **Antigravity's hook** uses the same committed command as on macOS (above); it needs `git` (which
  brings `sh`) and Python on `PATH`.
- **Host tools.** Give each tool a `windows` install command, and `env`, `aliases` or `paths` where
  it isn't on `PATH` (a Godot download, say). The kit's own core tools have winget commands.
- **Not handled:** finalize's CI steps run `python3 .specify/extensions/finalize/scripts/checks.py`;
  on Windows that needs a `python3` that runs (a Python from the Store provides one; from
  python.org, edit those two steps in the repository's `finalize-config.yml` to `py -3`).

What was verified: everything above runs on macOS (the unit tests, a full `bootstrap.sh` run twice
on a new repository, the hook through the Antigravity CLI). Windows' side was simulated in the tests
(a `sys.platform` of `win32`, the `windows` entries chosen, links that can't be made, CRLF
checkouts, a `python3` that doesn't run) and is otherwise reasoned, not run: Git Bash's handling of
the script, `pwd -W`, Python's `%VAR%` expansion, `os.symlink` without the privilege, Git for
Windows running an alias through its `sh`, and the shell Antigravity uses for hooks on Windows.

## What it installs

| Piece | Kind | What it does |
| --- | --- | --- |
| `dated-specs` | Spec Kit extension | Before `/speckit-specify`, names the folder `specs/YYYY-MM-DD-<name>/` (numbers collide between parallel branches; dates don't) |
| `finalize` | Spec Kit extension | `/speckit-finalize-run`: converge → design sync → verify → test → review → archive → push → branch CI → open the PR → PR checks, ending with a green PR handed to people to review and merge. It never merges. The spec moves to `specs/done/` inside that PR, so it lands there exactly when the feature merges. After the merge, `--from cleanup` lists the feature's worktrees and removes only the paths the person approves (no `--force`, branches kept). It owns repair: a failed check, test or conflict is diagnosed, fixed and pushed again. The steps are a list in `finalize-config.yml`; `scripts/checks.py branch\|pr` waits quietly for CI (one JSON result) |
| `templates/` | Spec Kit template overrides (`.specify/templates/overrides/`), kept current by the kit | Plan template: "Design context" (design docs read, updated or created) and "Significant decisions" (each one becomes an ADR). Tasks template: tests are required, and every list ends with design docs, agent guidance, ADRs, verify and finalize |
| `sdd` | Spec Kit workflow | `specify workflow run sdd`: specify → plan → tasks → implement → finalize to a green PR, stopping for a person to approve the spec and the plan, and to review and merge the PR; then the worktree cleanup offer |
| `pr-conflicts` | Skill, in this kit (`skills/`) | Brings the user's own conflicting or stale PRs back to mergeable: merge the base branch (never rebase), resolve each conflict by intent and ask when unclear, verify, test, push, explain in one comment. Finalize's push step uses it for real conflicts |
| `canon-review`, `canon-pr-review`, `canon-design-review` | Skills from [npow/claude-skills](https://github.com/npow/claude-skills), pinned in `kit-skills.json` | Finalize's review step: a code and design review of the diff against engineering practices, with an APPROVE / REQUEST_CHANGES / BLOCK decision. A repository replaces one by listing a skill of the same name in its `repo-kit.json` |
| `tools/prerequisites.py` | Script, kept current by the kit (`lib/prerequisites.py`) | Checks the host tools listed in `repo-kit.json` `prerequisites`, plus the kit's own |
| `scaffold/` | Files, copied only if missing | READMEs for `specs/`, `docs/design/` (with `TEMPLATE.md`), `docs/decisions/` and `docs/analysis/` |

Spec Kit writes each agent's own copy of the commands (`.claude/skills/`, and `.agents/skills/`
for both Codex and Antigravity), so a single kit serves all three.

## After bootstrapping

1. Write the constitution: `/speckit-constitution`. The plan template checks every plan against
   each of its principles.
2. Make sure the `verify` and `test` commands work. If they aren't `make verify` and
   `make test`, set `commands` in `repo-kit.json` and rerun; the kit writes them into finalize's
   steps and `AGENTS.md`. Other step changes go in
   `.specify/extensions/finalize/finalize-config.yml`, which is meant to be edited per repo.
3. List the repository's host tools under `prerequisites` and run
   `python3 tools/prerequisites.py --group all` (`py -3 ...` on Windows).
4. Commit `.specify/`, `.claude/`, `.codex/`, `.agents/`, `tools/prerequisites.py`, `AGENTS.md`,
   `CLAUDE.md`, `.gitignore`, `.gitattributes`, `repo-kit.json`, `repo-kit.lock.json` and the
   record folders.

Design docs aren't written up front. Each one appears when a feature first builds or reshapes
that area: the plan names it under "Design context", and `finalize` (step `design-sync`) writes it
from `docs/design/TEMPLATE.md`.

## Why Spec Kit's own packaging

Extensions and workflows are Spec Kit's native ways to extend it. They install with
`specify ... add --dev <folder>` and are rendered for every agent the repository uses, so nothing
here is specific to Claude or Codex. The plan and tasks templates are the exception: they go in as
the repository's template overrides rather than a preset, because with any preset installed Spec
Kit 1.1's scripts need PyYAML in the system `python3` (Homebrew and most stock Pythons lack it, and
installing into them needs `--break-system-packages`). Overrides need nothing, and the kit keeps
them current the same way as `finalize-config.yml`. A Claude or Codex plugin would serve only one agent, and CI
couldn't see it.

A Spec Kit **bundle** (`bundle.yml`, one `specify bundle install` for everything) is the next step.
In Spec Kit 1.1.1, a bundle installs its extensions and presets only from a published catalog, not
from a local folder. Once the extensions are published (zip releases plus a catalog
JSON), a bundle can replace `bootstrap.sh`.

## Changing the kit

- Change the files here, bump the component's `version`, then run `bootstrap.sh --refresh` in
  each repository. Tests: `python3 -m unittest discover tests` and
  `python3 -m unittest discover extensions/finalize/tests`.
- `finalize-config.yml`, the template overrides and `tools/prerequisites.py` belong to each
  repository once installed, and Spec Kit keeps them on reinstall. The kit updates it only while the repository hasn't edited it: `repo-kit.lock.json`
  records the version the kit last wrote. An edited copy is left alone, and the next run prints
  the `diff` command against the kit's new version, once per kit change.
- In a target repository, never edit the generated `speckit-*` skills by hand. Change the
  extension's command file here, or the repository's copy in `.specify/templates/overrides/`
  (the kit then stops updating that copy and reports its new versions instead).
