"""Apply a target repository's repo-kit.json for bootstrap.sh. Standard library only (Python 3.9+).

  repo_kit_config.py check <config> <repo>              fail on a malformed config, change nothing
  repo_kit_config.py agents <config>                    print the agents, comma-separated
  repo_kit_config.py network <config>                   print the network choice: full, off or unset
  repo_kit_config.py set-network <config> <full|off>    record the choice in the config
  repo_kit_config.py apply <config> <repo>              skills, permissions, worktrees, agent context
  repo_kit_config.py commands <config>                  print the verify and test commands, one per line
  repo_kit_config.py sync-file <config> <kit-file> <repo> <path>
                                                        update a kit-supplied file the repo has not edited

Every write is idempotent and prints one line per change. The kit owns only what it writes: the
rules and plugins it lists in .claude/settings.json, a marked block in .codex/config.toml and in
AGENTS.md, the "repo-kit-permissions" hook in .agents/hooks.json with its scripts in
.agents/repo-kit/, one line each in .gitignore and .gitattributes, and the skills named in the
config. Everything else in those files is left alone. Files are written as UTF-8 with LF line ends
on every platform, so a Windows checkout and a macOS one write the same bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_AGENTS = ["claude", "codex"]
KNOWN_AGENTS = {"claude": "Claude Code", "codex": "Codex", "agy": "Antigravity"}
NETWORK_CHOICES = {"full", "off"}
# permissions.commands: "ask" (the agent's own prompts) or "all" (every shell command without
# asking). Only Antigravity reads it so far; Claude Code and Codex keep the rules below.
COMMAND_CHOICES = {"ask", "all"}
# The repository's named check commands. finalize-config.yml's shell steps are rendered from them.
DEFAULT_COMMANDS = {"verify": "make verify", "test": "make test"}
VERSION = re.compile(r"^\d+(\.\d+)*$")
# prerequisites: install commands and candidate paths are per platform (tools/prerequisites.py).
PLATFORMS = ("mac", "linux", "windows")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
WORKTREES = ".claude/worktrees"
LOCK = "repo-kit.lock.json"
# Skills every repository gets from the kit (finalize's review step uses canon-review, its push
# step pr-conflicts). A repository lists a skill of the same name in its own config to replace one.
# A `kit:<path>` source is a folder inside this kit, so the lock never records a machine's path.
KIT = Path(__file__).resolve().parent.parent
KIT_SKILLS = KIT / "kit-skills.json"

# Claude Code rules the kit manages: `apply` adds the ones the config asks for and removes the
# others, so turning the network off takes its rules away again.
CLAUDE_BASE_RULES = ["Edit(/**)", "Bash(git worktree *)"]
CLAUDE_NETWORK_RULES = ["WebFetch(domain:*)", "WebSearch"]

# Antigravity reads permission rules only from the user's home (~/.gemini/...), never from the
# repository, so the kit grants through a workspace hook instead: .agents/hooks.json routes tool
# calls to .agents/repo-kit/approve.py, which answers "auto_approve" for what the config grants.
# The kit owns that one named hook and that folder; other hooks in the file are left alone.
AGY_HOOK = "repo-kit-permissions"
AGY_HOOKS = ".agents/hooks.json"
AGY_SCRIPT = ".agents/repo-kit/approve.py"
AGY_LAUNCHER = ".agents/repo-kit/approve.sh"
AGY_FILES = {AGY_SCRIPT: KIT / "lib/antigravity_approve.py",
             AGY_LAUNCHER: KIT / "lib/antigravity_approve.sh"}
AGY_SCRIPT_SOURCE = AGY_FILES[AGY_SCRIPT]
# The hook's command, one line for every platform. Antigravity doesn't document the shell it runs
# hooks with: the CLI on macOS runs `sh -c` in .agents/, and on Windows it may be cmd or PowerShell.
# A git alias works the same in all of them (no $(...), quotes or && the others read differently),
# runs at the top of the checkout the session is in (so a worktree uses its own copy), and runs
# through git's sh, which Git for Windows ships too; the launcher then finds a Python.
AGY_ALIAS = "repo-kit-approve"
AGY_COMMAND = f'git -c "alias.{AGY_ALIAS}=!sh {AGY_LAUNCHER}" {AGY_ALIAS}'
AGY_MATCHERS = {
    # The documented tool names and the CLI's own (write_file, edit_file, read_file...).
    "files": "view_file|read_file|write_file|write_to_file|create_file|edit_file|"
             "replace_file_content|multi_replace_file_content|delete_file|list_dir|list_directory|"
             "find_by_name|grep_search",
    "commands": "run_command",
    "network": "read_url_content|search_web|web_search|browser_.*",
}

BEGIN, END = "repo-kit:start", "repo-kit:end"


def fail(message: str) -> None:
    sys.exit(f"repo-kit: {message}")


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 with LF line ends on every platform (text mode would write CRLF on Windows)."""
    path.write_bytes(text.encode("utf-8"))


def text_bytes(path: Path) -> bytes:
    """A file's bytes with CRLF read as LF, unless it is binary.

    Git for Windows checks text out with CRLF by default (core.autocrlf), so the same committed
    file reads differently there; digests use this so a checkout on Windows isn't taken for an edit.
    """
    data = path.read_bytes()
    return data if b"\0" in data else data.replace(b"\r\n", b"\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, json.dumps(data, indent=2) + "\n")


def load(path: Path) -> dict:
    config = read_json(path)
    if not isinstance(config, dict):
        fail(f"{path}: expected a JSON object")
    return config


# -- validation ------------------------------------------------------------------------------


def agents(config: dict) -> list[str]:
    names = config.get("agents", DEFAULT_AGENTS)
    if not names or any(n not in KNOWN_AGENTS for n in names):
        fail(f"agents must be a non-empty list of {sorted(KNOWN_AGENTS)}, got {names}")
    return names


def network(config: dict) -> str:
    choice = config.get("permissions", {}).get("network", "unset")
    if choice not in NETWORK_CHOICES | {"unset"}:
        fail(f"permissions.network must be one of {sorted(NETWORK_CHOICES)}, got {choice!r}")
    return choice


def command_policy(config: dict) -> str:
    permissions = config.get("permissions", {})
    if not isinstance(permissions, dict):
        fail(f"permissions must be an object, got {permissions!r}")
    choice = permissions.get("commands", "ask")
    if choice not in COMMAND_CHOICES:
        fail(f"permissions.commands must be one of {sorted(COMMAND_CHOICES)}, got {choice!r}")
    return choice


def commands(config: dict) -> dict[str, str]:
    given = config.get("commands", {})
    if not isinstance(given, dict) or set(given) - set(DEFAULT_COMMANDS):
        fail(f"commands may name only {sorted(DEFAULT_COMMANDS)}, got {given!r}")
    for key, value in given.items():
        if not isinstance(value, str) or not value.strip() or "\n" in value:
            fail(f"commands.{key} must be one shell command line, got {value!r}")
    return {**DEFAULT_COMMANDS, **given}


def prerequisites(config: dict) -> None:
    section = config.get("prerequisites", {})
    if not isinstance(section, dict) or not isinstance(section.get("groups", {}), dict):
        fail("prerequisites must be an object with a groups object")
    for group, body in section.get("groups", {}).items():
        if not SKILL_NAME.match(group) or not isinstance(body, dict):
            fail(f"prerequisites group {group!r} must be a lowercase name mapping to an object")
        for tool in body.get("tools", []):
            if not isinstance(tool, dict) or not tool.get("name"):
                fail(f"every tool in prerequisites group {group!r} needs a name")
            if "min" in tool and not VERSION.match(str(tool["min"])):
                fail(f"tool {tool['name']!r}: min must be a version like 1.2.3, got {tool['min']!r}")
            prerequisite_tool(tool)


def strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def prerequisite_tool(tool: dict) -> None:
    """A tool entry's optional keys, by type (tools/prerequisites.py trusts them)."""
    name = tool["name"]
    if not isinstance(name, str):
        fail(f"tool name {name!r} must be a string")
    if "args" in tool and not (isinstance(tool["args"], list)
                               and all(isinstance(a, str) for a in tool["args"])):
        fail(f"tool {name!r}: args must be a list of strings")
    for key in (*PLATFORMS, "then"):
        if key in tool and not (isinstance(tool[key], str) and tool[key].strip()):
            fail(f"tool {name!r}: {key} must be a command line (a string)")
    if "env" in tool and not (isinstance(tool["env"], str) and ENV_NAME.match(tool["env"])):
        fail(f"tool {name!r}: env must name an environment variable, like GODOT, got {tool['env']!r}")
    if "aliases" in tool and not strings(tool["aliases"]):
        fail(f"tool {name!r}: aliases must be a list of executable names")
    if "paths" in tool:
        paths = tool["paths"]
        if not isinstance(paths, dict) or set(paths) - set(PLATFORMS):
            fail(f"tool {name!r}: paths must map {', '.join(PLATFORMS)} to lists of paths, "
                 f"got {paths!r}")
        for platform, listed in paths.items():
            if not strings(listed):
                fail(f"tool {name!r}: paths.{platform} must be a list of paths (globs allowed)")


def claude_plugins(config: dict, settings: dict) -> tuple[dict, list[str]]:
    claude = config.get("claude", {})
    marketplaces = claude.get("marketplaces", {})
    plugins = claude.get("plugins", [])
    known = set(marketplaces) | set(settings.get("extraKnownMarketplaces", {}))
    for plugin in plugins:
        if plugin.count("@") != 1 or plugin.split("@")[1] not in known:
            fail(f"plugin {plugin!r} must be <name>@<marketplace>, with the marketplace declared")
    return marketplaces, plugins


def is_git(source: str) -> bool:
    return source.startswith(("https://", "git@", "ssh://")) or source.endswith(".git")


def skills(config: dict) -> list[dict]:
    own = config.get("skills", [])
    names = {entry.get("name") for entry in own}
    kit = [entry for entry in read_json(KIT_SKILLS).get("skills", []) if entry["name"] not in names]
    entries = kit + own
    seen = set()
    for entry in entries:
        name = entry.get("name", "")
        if not SKILL_NAME.match(name) or name.startswith("speckit-"):
            fail(f"skill name {name!r} must be lowercase letters, digits and hyphens, not speckit-*")
        if name in seen:
            fail(f"skill {name!r} is listed twice")
        seen.add(name)
        if not entry.get("source"):
            fail(f"skill {name!r} needs a source: a git URL or a local path")
        if is_git(entry["source"]) and not entry.get("ref"):
            fail(f"skill {name!r} comes from git, so it needs a ref (tag, branch or commit)")
    return entries


def check(config: dict, repo: Path) -> None:
    agents(config)
    command_policy(config)
    network(config)
    commands(config)
    prerequisites(config)
    claude_plugins(config, read_json(repo / ".claude/settings.json"))
    skills(config)


# -- skills ----------------------------------------------------------------------------------


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + text_bytes(path) + b"\0")
    return "sha256:" + digest.hexdigest()


def fetch_skill(entry: dict, config_dir: Path, work: Path) -> tuple[Path, str]:
    """Return the skill's folder and the revision it came from (a commit, or "local")."""
    source, sub = entry["source"], entry.get("path", "")
    if source.startswith("kit:"):
        folder = (KIT / source[len("kit:"):] / sub).resolve()
        if not (folder / "SKILL.md").is_file():
            fail(f"skill {entry['name']!r}: no SKILL.md in {folder}")
        return folder, "kit"
    if not is_git(source):
        folder = (config_dir / source / sub).resolve()
        if not (folder / "SKILL.md").is_file():
            fail(f"skill {entry['name']!r}: no SKILL.md in {folder}")
        return folder, "local"
    clone = work / entry["name"]
    git = ["git", "-C", str(clone)]
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    subprocess.run(git + ["fetch", "-q", "--depth", "1", source, entry["ref"]], check=True)
    subprocess.run(git + ["checkout", "-q", "FETCH_HEAD"], check=True)
    commit = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    folder = clone / sub
    if not (folder / "SKILL.md").is_file():
        fail(f"skill {entry['name']!r}: no SKILL.md at {sub or 'the root'} of {source}@{entry['ref']}")
    return folder, commit.stdout.strip()


def is_placeholder(path: Path, want: Path) -> bool:
    """Git's stand-in for a symbolic link on a checkout without them (Windows, unless symlinks are
    on): a small file holding the link's target."""
    return path.is_file() and not path.is_symlink() and text_bytes(path).strip() == want.as_posix().encode()


def link_for_claude(repo: Path, name: str, changed: list[str], kit_digests: set) -> None:
    """Make .claude/skills/<name> a relative link to .agents/skills/<name>.

    Where links can't be made (Windows without Developer Mode), the folder is copied instead and
    the person is told not to commit the copy. A copy the kit made (its digest is one the lock
    records) is replaced freely, by a link once this machine can make one; any other folder there
    is refused.
    """
    link = repo / ".claude/skills" / name
    want = Path("../../.agents/skills") / name
    source = repo / ".agents/skills" / name
    was_copy = False
    if link.is_symlink():
        if Path(os.readlink(link)) == want:
            return
        link.unlink()
    elif is_placeholder(link, want):
        link.unlink()
    elif link.is_dir():
        digest = tree_digest(link)
        if digest not in kit_digests:
            fail(f".claude/skills/{name} is a real folder; the kit links it to .agents/skills/{name}")
        was_copy = digest == tree_digest(source)
        shutil.rmtree(link)
    elif link.exists():
        fail(f".claude/skills/{name} is a file; the kit links it to .agents/skills/{name}")
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(want, target_is_directory=True)
        changed.append(f"linked .claude/skills/{name}")
    except OSError:
        shutil.copytree(source, link)
        if not was_copy:
            changed.append(f"copied .claude/skills/{name}: this machine can't make symbolic links "
                           "(on Windows, turn on Developer Mode and run git config core.symlinks "
                           "true); don't commit the copy")


def remove_claude_link(repo: Path, name: str, digest: str) -> None:
    link = repo / ".claude/skills" / name
    if link.is_symlink() or is_placeholder(link, Path("../../.agents/skills") / name):
        link.unlink()
    elif link.is_dir() and tree_digest(link) == digest:
        shutil.rmtree(link)


def apply_skills(config: dict, config_dir: Path, repo: Path, use_claude: bool) -> list[str]:
    """Copy each listed skill into .agents/skills/<name>/ and link it for Claude Code.

    Codex and Antigravity read .agents/skills/ directly; Claude Code reads .claude/skills/, so each
    skill gets a relative symlink there, never a second copy (unless this machine can't make links:
    see link_for_claude). repo-kit.lock.json records where each
    copy came from and its digest, so a hand edit is refused rather than silently overwritten. A
    git skill is fetched again only when its pin changes; a local one on every run.
    """
    entries = skills(config)
    lock_path = repo / LOCK
    lock = dict(read_json(lock_path).get("skills", {}))
    changed: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for entry in entries:
            name = entry["name"]
            target = repo / ".agents/skills" / name
            pin = {k: entry.get(k, "") for k in ("source", "ref", "path")}
            recorded = lock.get(name, {})
            if target.exists() and recorded.get("digest") != tree_digest(target):
                fail(f".agents/skills/{name} was changed by hand or not installed by the kit: "
                     "move the change upstream or delete the folder, then rerun")
            current = target.exists() and {k: recorded.get(k, "") for k in pin} == pin
            if not (current and is_git(pin["source"])):
                folder, revision = fetch_skill(entry, config_dir, Path(tmp))
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(folder, target, ignore=shutil.ignore_patterns(".git"))
                digest = tree_digest(target)
                if recorded.get("digest") != digest:
                    changed.append(f"skill {name} ({revision[:12]})")
                lock[name] = {**pin, "revision": revision, "digest": digest}
            if use_claude:
                digests = {recorded.get("digest"), lock.get(name, {}).get("digest")} - {None}
                link_for_claude(repo, name, changed, digests)
        for name in sorted(set(lock) - {e["name"] for e in entries}):
            shutil.rmtree(repo / ".agents/skills" / name, ignore_errors=True)
            remove_claude_link(repo, name, lock[name].get("digest", ""))
            del lock[name]
            changed.append(f"removed skill {name}")
    data = read_json(lock_path)
    if data.get("skills", {}) != lock:
        write_json(lock_path, {**data, "skills": lock})
    return changed


# -- permissions and worktrees ---------------------------------------------------------------


def apply_claude(config: dict, repo: Path, choice: str) -> list[str]:
    path = repo / ".claude/settings.json"
    settings = read_json(path)
    before = json.dumps(settings, sort_keys=True)
    changed = []

    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    wanted = CLAUDE_BASE_RULES + (CLAUDE_NETWORK_RULES if choice == "full" else [])
    for rule in CLAUDE_BASE_RULES + CLAUDE_NETWORK_RULES:
        if rule in wanted and rule not in allow:
            allow.append(rule)
            changed.append(f"allow {rule}")
        elif rule not in wanted and rule in allow:
            allow.remove(rule)
            changed.append(f"no longer allow {rule}")

    marketplaces, plugins = claude_plugins(config, settings)
    for name, source in marketplaces.items():
        declared = settings.setdefault("extraKnownMarketplaces", {})
        if declared.get(name) != {"source": source}:
            declared[name] = {"source": source}
            changed.append(f"marketplace {name}")
    for plugin in plugins:
        enabled = settings.setdefault("enabledPlugins", {})
        if enabled.get(plugin) is not True:
            enabled[plugin] = True
            changed.append(f"plugin {plugin}")

    if json.dumps(settings, sort_keys=True) != before:
        write_json(path, settings)
    return [f".claude/settings.json: {c}" for c in changed]


def codex_block(choice: str) -> str:
    # Dotted keys, not a [table] header: the block sits at the top of the file and must not
    # capture the top-level keys that follow it.
    lines = [
        f"# {BEGIN}: written by speckit-repo-kit bootstrap.sh from repo-kit.json; change that file",
        "# Writes inside the repository. .git is writable so worktrees and commits work; start Codex",
        "# at the main checkout, since a session started inside a worktree cannot write its git data.",
        'sandbox_mode = "workspace-write"',
        'approval_policy = "on-request"',
        'sandbox_workspace_write.writable_roots = [".git"]',
        f"sandbox_workspace_write.network_access = {'true' if choice == 'full' else 'false'}",
        f'web_search = "{"live" if choice == "full" else "disabled"}"',
        f"# {END}",
    ]
    return "\n".join(lines) + "\n"


def replace_block(text: str, block: str, opening: str, closing: str, at_top: bool) -> str:
    pattern = re.compile(re.escape(opening) + r".*?" + re.escape(closing) + r"[^\n]*\n?", re.S)
    if pattern.search(text):
        return pattern.sub(lambda _: block, text, count=1)
    if at_top:
        return block + ("\n" + text if text else "")
    return text.rstrip("\n") + "\n\n" + block if text.strip() else block


def apply_codex(repo: Path, choice: str) -> list[str]:
    path = repo / ".codex/config.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    outside = re.sub(re.escape(f"# {BEGIN}") + r".*?" + re.escape(f"# {END}") + r"\n?", "", text, flags=re.S)
    clash = re.search(r"^\s*(sandbox_mode|approval_policy|web_search|\[sandbox_workspace_write\]|"
                      r"sandbox_workspace_write\.)", outside, re.M)
    if clash:
        fail(f".codex/config.toml sets {clash.group(1).strip()} outside the kit's block; remove it "
             "so the block is the one place these are set")
    new = replace_block(text, codex_block(choice), f"# {BEGIN}", f"# {END}", at_top=True)
    if new == text:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, new)
    return [f".codex/config.toml: workspace writes, .git writable, network {choice}"]


def agy_hook(config: dict, choice: str) -> dict:
    """The kit's entry in .agents/hooks.json: one PreToolUse group per grant."""
    groups = ["files"]
    if command_policy(config) == "all":
        groups.append("commands")
    if choice == "full":
        groups.append("network")
    return {"PreToolUse": [
        {"matcher": AGY_MATCHERS[group],
         "hooks": [{"type": "command", "command": f"{AGY_COMMAND} {group}", "timeout": 10}]}
        for group in groups
    ]}


def apply_agy(config: dict, repo: Path, choice: str) -> list[str]:
    changed = []
    for relative, kit_file in AGY_FILES.items():
        script = repo / relative
        source = text_bytes(kit_file)
        # Raw bytes: a launcher checked out with CRLF (Git for Windows' default) breaks sh.
        if not script.exists() or script.read_bytes() != source:
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_bytes(source)
            script.chmod(0o755)
            changed.append(f"{relative}: the kit's approval hook")

    path = repo / AGY_HOOKS
    try:
        hooks = read_json(path)
    except json.JSONDecodeError as error:
        fail(f"{AGY_HOOKS} is not valid JSON ({error}); fix it so the kit can add its hook")
    if not isinstance(hooks, dict):
        fail(f"{AGY_HOOKS}: expected a JSON object of named hooks")
    entry = agy_hook(config, choice)
    if hooks.get(AGY_HOOK) != entry:
        hooks[AGY_HOOK] = entry
        write_json(path, hooks)
        granted = ["files in the repository"]
        if command_policy(config) == "all":
            granted.append("all shell commands")
        if choice == "full":
            granted.append("web and browser")
        changed.append(f"{AGY_HOOKS}: approve {', '.join(granted)}")
    return changed


def apply_gitignore(repo: Path) -> list[str]:
    path = repo / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    entry = f"/{WORKTREES}/"
    if entry in text.splitlines():
        return []
    write_text(path, text + ("\n" if text and not text.endswith("\n") else "") + entry + "\n")
    return [f".gitignore: {entry}"]


GITATTRIBUTES = "*.sh text eol=lf"


def apply_gitattributes(repo: Path) -> list[str]:
    """Shell scripts keep LF line ends in every checkout: Spec Kit's scripts and the Antigravity
    hook's launcher run under sh (Git Bash on Windows), which fails on a CRLF checkout."""
    path = repo / ".gitattributes"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if GITATTRIBUTES in text.splitlines():
        return []
    write_text(path, text + ("\n" if text and not text.endswith("\n") else "") + GITATTRIBUTES + "\n")
    return [f".gitattributes: {GITATTRIBUTES}"]


# -- agent context ---------------------------------------------------------------------------


def context_block(config: dict, choice: str) -> str:
    external = ", ".join(f"`{e['name']}`" for e in skills(config)) or "none yet"
    checks = commands(config)
    network_line = {
        "full": "allowed: web fetch and search, and network access from shell commands.",
        "off": "off. Ask before fetching anything, and say what for.",
    }[choice]
    names = agents(config)
    where = {
        "claude": "`.claude/settings.json` (Claude Code)",
        "codex": "`.codex/config.toml` (Codex)",
        "agy": f"`{AGY_HOOKS}` (Antigravity: a hook, `{AGY_SCRIPT}`, approves them)",
    }
    files = [where[n] for n in names]
    places = files[0] if len(files) == 1 else ", ".join(files[:-1]) + " and " + files[-1]
    trust_line = ""
    trusting = [KNOWN_AGENTS[n] for n in names if n in ("claude", "codex")]
    if trusting:
        verb = "applies its rules" if len(trusting) == 1 else "apply theirs"
        trust_line += f"\n  {' and '.join(trusting)} {verb} once the person approves trusting this folder."
    if "agy" in names:
        trust_line += ("\n  Antigravity loads the hook from the workspace itself; rules in the person's own\n"
                       "  Antigravity settings still win over it.")
    shell_line = ""
    if "agy" in names and command_policy(config) == "all":
        shell_line = ("\n- **Shell commands.** Antigravity runs any shell command in this repository without "
                      "asking\n  (`permissions.commands` is `all`). Other agents ask as their own settings say.")
    return f"""<!-- {BEGIN}: written by speckit-repo-kit bootstrap.sh from repo-kit.json; change that file, not this section -->
## Agent setup

Agents: {', '.join(KNOWN_AGENTS[n] for n in names)}. Settings come from `repo-kit.json`;
rerun the kit's `bootstrap.sh` after changing it.

- **Checks.** Verify: `{checks['verify']}` (static checks, after each edit). Test: `{checks['test']}`
  (before pushing). Finalize runs both. Host tools: `python3 tools/prerequisites.py` (`py -3` on
  Windows; add `--group all` for every optional group).
- **Writing.** Edit any file in this repository without asking. Nothing outside it.{shell_line}
- **Network** is {network_line}
- **Worktrees.** Parallel work happens in git worktrees under `{WORKTREES}/<name>/` (gitignored),
  one branch each: `git worktree add {WORKTREES}/<name> -b <branch>`. Claude Code's
  `--worktree <name>` and subagent worktrees use the same place. Start Codex at the main checkout
  and work in a worktree from there: Codex cannot write the git data of a worktree it was started
  in. Remove a worktree only after its branch has merged and the person approved removing that
  path (finalize's cleanup step asks): `git worktree remove {WORKTREES}/<name>`, never `--force`,
  keeping the local branch. Never sweep worktrees nobody approved.
- **Conflicts.** Bring a branch up to date by merging the base branch, never by rebasing or
  force-pushing; resolve conflicts by intent and ask when unclear (skill `pr-conflicts`).
- **Skills.** Each skill has one copy, in `.agents/skills/<name>/`: Codex and Antigravity read it
  there, and `.claude/skills/<name>` links to it. Skills installed by the kit or from other
  repositories ({external}) are recorded in `repo-kit.lock.json`: never edit them here; change
  them where they come from (the kit, or the pinned repository) and rerun the kit. Spec Kit's `speckit-*` skills are generated: never
  edit them by hand.
- **Permissions** live in {places}.{trust_line}
<!-- {END} -->
"""


def apply_context(config: dict, repo: Path, choice: str) -> list[str]:
    changed = []
    path = repo / "AGENTS.md"
    text = path.read_text(encoding="utf-8") if path.exists() else "# Agent instructions\n"
    new = replace_block(text, context_block(config, choice), f"<!-- {BEGIN}", f"<!-- {END}", at_top=False)
    if new != text:
        write_text(path, new)
        changed.append("AGENTS.md: agent setup section")
    if "claude" in agents(config):
        claude_md = repo / "CLAUDE.md"
        if not claude_md.exists():
            write_text(claude_md, "@AGENTS.md\n")
            changed.append("CLAUDE.md: imports AGENTS.md")
        elif "AGENTS.md" not in claude_md.read_text(encoding="utf-8"):
            changed.append("CLAUDE.md does not mention AGENTS.md: add the line @AGENTS.md")
    return changed


# -- kit-supplied files the repository may edit -----------------------------------------------


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(text_bytes(path)).hexdigest()


def render(kit_file: Path, config: dict) -> bytes:
    """The kit's file as this repository gets it: shell steps run the repository's own commands."""
    text = kit_file.read_text(encoding="utf-8")
    for key, command in commands(config).items():
        text = text.replace(f"run: $ {DEFAULT_COMMANDS[key]}\n", f"run: $ {command}\n")
    return text.encode()


def sync_file(kit_file: Path, config: dict, repo: Path, relative: str) -> list[str]:
    """Bring a kit-supplied file (finalize-config.yml) up to the kit's version unless edited here.

    Spec Kit keeps an installed extension's config on reinstall, which protects a repository's own
    edits but also holds back the kit's changes. The lock records the digest the kit last wrote:
    a file still at that digest has not been edited and takes the kit's version; an edited one is
    left alone and reported, so the person can compare and merge.
    """
    target = repo / relative
    lock_path = repo / LOCK
    data = read_json(lock_path)
    files = data.setdefault("files", {})
    content = render(kit_file, config)
    kit = "sha256:" + hashlib.sha256(content).hexdigest()
    if not target.exists() or file_digest(target) == kit:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            shutil.copymode(kit_file, target)
        changed = [] if files.get(relative) == kit else [f"{relative}: the kit's version"]
    elif file_digest(target) == files.get(relative):
        target.write_bytes(content)
        changed = [f"{relative}: updated to the kit's version"]
    else:
        # Edited here: report each new kit version once, then stay quiet until the next one.
        noticed = data.setdefault("noticed", {})
        if noticed.get(relative) == kit:
            return []
        noticed[relative] = kit
        write_json(lock_path, data)
        return [f"{relative} was edited here and the kit's version changed; compare with: "
                f"diff {target} {kit_file} (the kit renders its shell steps from repo-kit.json "
                "commands)"]
    files[relative] = kit
    write_json(lock_path, data)
    return changed


# -- entry point -----------------------------------------------------------------------------


def apply(config_path: Path, repo: Path) -> list[str]:
    config = load(config_path)
    check(config, repo)
    choice = network(config)
    if choice == "unset":
        fail("permissions.network is not set: full or off")
    names = agents(config)
    changed = apply_skills(config, config_path.parent, repo, "claude" in names)
    if "claude" in names:
        changed += apply_claude(config, repo, choice)
    if "codex" in names:
        changed += apply_codex(repo, choice)
    if "agy" in names:
        changed += apply_agy(config, repo, choice)
    changed += apply_gitignore(repo)
    changed += apply_gitattributes(repo)
    changed += apply_context(config, repo, choice)
    return changed


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    command, path = args[0], Path(args[1])
    if command == "check" and len(args) == 3:
        check(load(path), Path(args[2]))
    elif command == "agents" and len(args) == 2:
        print(",".join(agents(load(path))))
    elif command == "network" and len(args) == 2:
        print(network(load(path)))
    elif command == "set-network" and len(args) == 3 and args[2] in NETWORK_CHOICES:
        config = load(path)
        config.setdefault("permissions", {})["network"] = args[2]
        write_json(path, config)
    elif command == "commands" and len(args) == 2:
        print("\n".join(commands(load(path)).values()))
    elif command == "sync-file" and len(args) == 5:
        for line in sync_file(Path(args[2]), load(path), Path(args[3]), args[4]):
            print(line)
    elif command == "apply" and len(args) == 3:
        for line in apply(path, Path(args[2])):
            print(line)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
