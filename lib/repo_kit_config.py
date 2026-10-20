"""Apply a target repository's repo-kit.json for bootstrap.sh. Standard library only (Python 3.9+).

  repo_kit_config.py check <config> <repo>              fail on a malformed config, change nothing
  repo_kit_config.py agents <config>                    print the agents, comma-separated
  repo_kit_config.py network <config>                   print the network choice: full, off or unset
  repo_kit_config.py set-network <config> <full|off>    record the choice in the config
  repo_kit_config.py apply <config> <repo>              skills, permissions, worktrees, agent context
  repo_kit_config.py sync-file <kit-file> <repo> <path> update a kit-supplied file the repo has not edited

Every write is idempotent and prints one line per change. The kit owns only what it writes: the
rules and plugins it lists in .claude/settings.json, a marked block in .codex/config.toml and in
AGENTS.md, and the skills named in the config. Everything else in those files is left alone.
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

BEGIN, END = "repo-kit:start", "repo-kit:end"


def fail(message: str) -> None:
    sys.exit(f"repo-kit: {message}")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


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
    network(config)
    claude_plugins(config, read_json(repo / ".claude/settings.json"))
    skills(config)


# -- skills ----------------------------------------------------------------------------------


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
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


def link_for_claude(repo: Path, name: str, changed: list[str]) -> None:
    link = repo / ".claude/skills" / name
    want = Path("../../.agents/skills") / name
    if link.is_symlink() and Path(os.readlink(link)) == want:
        return
    if link.exists() and not link.is_symlink():
        fail(f".claude/skills/{name} is a real folder; the kit links it to .agents/skills/{name}")
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    link.symlink_to(want)
    changed.append(f"linked .claude/skills/{name}")


def apply_skills(config: dict, config_dir: Path, repo: Path, use_claude: bool) -> list[str]:
    """Copy each listed skill into .agents/skills/<name>/ and link it for Claude Code.

    Codex and Antigravity read .agents/skills/ directly; Claude Code reads .claude/skills/, so each
    skill gets a relative symlink there, never a second copy. repo-kit.lock.json records where each
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
                link_for_claude(repo, name, changed)
        for name in sorted(set(lock) - {e["name"] for e in entries}):
            shutil.rmtree(repo / ".agents/skills" / name, ignore_errors=True)
            link = repo / ".claude/skills" / name
            if link.is_symlink():
                link.unlink()
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
    text = path.read_text() if path.exists() else ""
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
    path.write_text(new)
    return [f".codex/config.toml: workspace writes, .git writable, network {choice}"]


def apply_gitignore(repo: Path) -> list[str]:
    path = repo / ".gitignore"
    text = path.read_text() if path.exists() else ""
    entry = f"/{WORKTREES}/"
    if entry in text.splitlines():
        return []
    path.write_text(text + ("\n" if text and not text.endswith("\n") else "") + entry + "\n")
    return [f".gitignore: {entry}"]


# -- agent context ---------------------------------------------------------------------------


def context_block(config: dict, choice: str) -> str:
    external = ", ".join(f"`{e['name']}`" for e in skills(config)) or "none yet"
    network_line = {
        "full": "allowed: web fetch and search, and network access from shell commands.",
        "off": "off. Ask before fetching anything, and say what for.",
    }[choice]
    return f"""<!-- {BEGIN}: written by speckit-repo-kit bootstrap.sh from repo-kit.json; change that file, not this section -->
## Agent setup

Agents: {', '.join(KNOWN_AGENTS[n] for n in agents(config))}. Settings come from `repo-kit.json`;
rerun the kit's `bootstrap.sh` after changing it.

- **Writing.** Edit any file in this repository without asking. Nothing outside it.
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
- **Permissions** live in `.claude/settings.json` and `.codex/config.toml`. Each person approves
  them once, when their agent first asks to trust this folder.
<!-- {END} -->
"""


def apply_context(config: dict, repo: Path, choice: str) -> list[str]:
    changed = []
    path = repo / "AGENTS.md"
    text = path.read_text() if path.exists() else "# Agent instructions\n"
    new = replace_block(text, context_block(config, choice), f"<!-- {BEGIN}", f"<!-- {END}", at_top=False)
    if new != text:
        path.write_text(new)
        changed.append("AGENTS.md: agent setup section")
    if "claude" in agents(config):
        claude_md = repo / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text("@AGENTS.md\n")
            changed.append("CLAUDE.md: imports AGENTS.md")
        elif "AGENTS.md" not in claude_md.read_text():
            changed.append("CLAUDE.md does not mention AGENTS.md: add the line @AGENTS.md")
    return changed


# -- kit-supplied files the repository may edit -----------------------------------------------


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def sync_file(kit_file: Path, repo: Path, relative: str) -> list[str]:
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
    kit = file_digest(kit_file)
    if not target.exists() or file_digest(target) == kit:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(kit_file, target)
        changed = [] if files.get(relative) == kit else [f"{relative}: the kit's version"]
    elif file_digest(target) == files.get(relative):
        shutil.copyfile(kit_file, target)
        changed = [f"{relative}: updated to the kit's version"]
    else:
        # Edited here: report each new kit version once, then stay quiet until the next one.
        noticed = data.setdefault("noticed", {})
        if noticed.get(relative) == kit:
            return []
        noticed[relative] = kit
        write_json(lock_path, data)
        return [f"{relative} was edited here and the kit's version changed; compare with: "
                f"diff {target} {kit_file}"]
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
    changed += apply_gitignore(repo)
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
    elif command == "sync-file" and len(args) == 4:
        for line in sync_file(path, Path(args[2]), args[3]):
            print(line)
    elif command == "apply" and len(args) == 3:
        for line in apply(path, Path(args[2])):
            print(line)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
