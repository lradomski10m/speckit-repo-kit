#!/usr/bin/env bash
# Set up Spec Kit in a repository with this kit's extensions, preset, workflow and record folders.
#
#   bootstrap.sh [--config <file>] [--network full|off] [--refresh] <repo-path>
#
# Reads <repo-path>/repo-kit.json (or --config): the agents to set up, external skills, Claude
# plugins and the network choice; see repo-kit.example.json. Without a file: Claude Code and Codex.
# Network access is never granted silently: with no choice recorded, the script asks (or takes
# --network) and writes the answer into the config file.
#
# Safe to run again: Spec Kit components already installed are skipped (--refresh reinstalls
# them from this kit), and record-folder files that already exist are never overwritten.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPECKIT_MIN="1.1.0"
config=""
network_flag=""
refresh=0
repo=""

usage() { sed -n '4p' "$0" | sed 's/^#   //' >&2; exit 2; }
while [ $# -gt 0 ]; do
    case "$1" in
        --config) config="${2:?}"; shift 2 ;;
        --network) network_flag="${2:?}"; shift 2 ;;
        --refresh) refresh=1; shift ;;
        -h|--help) usage ;;
        -*) echo "unknown option: $1" >&2; usage ;;
        *) repo="$1"; shift ;;
    esac
done
[ -n "$repo" ] || usage
repo="$(cd "$repo" && pwd)"

command -v specify >/dev/null || {
    echo "specify (Spec Kit CLI) is not on PATH: uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@v$SPECKIT_MIN" >&2
    exit 1
}
version="$(specify version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
if [ "$(printf '%s\n%s\n' "$SPECKIT_MIN" "$version" | sort -V | head -1)" != "$SPECKIT_MIN" ]; then
    echo "Spec Kit $version is older than $SPECKIT_MIN: specify self upgrade" >&2
    exit 1
fi
git -C "$repo" rev-parse --show-toplevel >/dev/null 2>&1 || { echo "$repo is not a git repository" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 (3.9+) is needed to read repo-kit.json" >&2; exit 1; }

[ -n "$config" ] && config="$(cd "$(dirname "$config")" && pwd)/$(basename "$config")"
config="${config:-$repo/repo-kit.json}"
cfg() { python3 -I "$KIT/lib/repo_kit_config.py" "$@"; }
cfg check "$config" "$repo"
agents="$(cfg agents "$config")"

# Network access: the person running this decides, once; the answer is kept in the config.
if [ -n "$network_flag" ]; then
    cfg set-network "$config" "$network_flag"
elif [ "$(cfg network "$config")" = unset ]; then
    if [ -t 0 ]; then
        read -r -p "Allow agents full network access (web fetch/search, network from shell commands)? [y/N] " answer
        case "$answer" in [yY]*) cfg set-network "$config" full ;; *) cfg set-network "$config" off ;; esac
    else
        echo "Network access is not decided: rerun with --network full or --network off" >&2
        exit 1
    fi
fi

cd "$repo"
IFS=',' read -r -a agent_list <<< "$agents"
first="${agent_list[0]}"

# 1. Spec Kit itself, for the first agent; then every other agent.
if [ ! -f .specify/integration.json ]; then
    echo "==> specify init ($first)"
    specify init --here --force --non-interactive --ignore-agent-tools --script sh --integration "$first"
fi
for agent in "${agent_list[@]}"; do
    if ! grep -q "\"$agent\"" .specify/integration.json; then
        echo "==> specify integration install $agent"
        # agy writes the same .agents/skills/ folder as codex, so Spec Kit wants --force for it.
        if [ "$agent" = "agy" ]; then
            specify integration install "$agent" --script sh --force
        else
            specify integration install "$agent" --script sh
        fi
    fi
done

# 2. This kit's components, from local folders.
# Read the whole listing before matching: with pipefail, grep -q closing the pipe early fails the check.
installed() { local out; out="$(specify "$1" list 2>&1)"; grep -qw -- "$2" <<< "$out"; }
for ext in dated-specs finalize; do
    if [ "$refresh" = 1 ] || ! installed extension "$ext"; then
        echo "==> extension $ext"
        specify extension add --dev "$KIT/extensions/$ext" --force
    fi
done
if [ "$refresh" = 1 ] || ! installed preset sdd-records; then
    echo "==> preset sdd-records"
    [ "$refresh" = 1 ] && specify preset remove sdd-records >/dev/null 2>&1 || true
    specify preset add --dev "$KIT/presets/sdd-records"
fi
if [ "$refresh" = 1 ] || ! installed workflow sdd; then
    echo "==> workflow sdd"
    [ "$refresh" = 1 ] && specify workflow remove sdd >/dev/null 2>&1 || true
    specify workflow add --dev "$KIT/workflows/sdd"
fi

# Spec Kit keeps an installed extension's config on reinstall; take the kit's version when the
# repository has not edited it.
cfg sync-file "$KIT/extensions/finalize/finalize-config.yml" "$repo" \
    .specify/extensions/finalize/finalize-config.yml | sed 's/^/==> /'

# Spec Kit registers an extension's commands only for the default integration; `use` registers
# them for each of the others too. The first agent is made the default again at the end.
for agent in "${agent_list[@]:1}" "$first"; do
    specify integration use "$agent" >/dev/null
done

# Spec Kit's agent-context extension rewrites a block of AGENTS.md/CLAUDE.md on every feature;
# this setup keeps agent files hand-written, so it is turned off.
if installed extension agent-context; then
    specify extension disable agent-context >/dev/null
fi

# 3. Record folders: only the files that do not exist yet.
(cd "$KIT/scaffold" && find . -type f) | while read -r f; do
    if [ ! -e "$f" ]; then
        mkdir -p "$(dirname "$f")"
        cp "$KIT/scaffold/$f" "$f"
        echo "==> created ${f#./}"
    fi
done

# 4. External skills, permissions (writing, network, worktrees), Claude plugins, and the agent
# context section in AGENTS.md that describes them.
cfg apply "$config" "$repo" | sed 's/^/==> /'

cat <<EOF

Done. Next:
  - write .specify/memory/constitution.md (/speckit-constitution);
  - make sure AGENTS.md names the verify and test commands, and edit the shell steps in
    .specify/extensions/finalize/finalize-config.yml if they are not 'make verify' and 'make test';
  - commit .specify/, .claude/, .codex/, .agents/, AGENTS.md, CLAUDE.md, .gitignore,
    repo-kit.json, repo-kit.lock.json and the record folders.
EOF
