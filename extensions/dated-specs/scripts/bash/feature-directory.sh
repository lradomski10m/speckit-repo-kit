#!/usr/bin/env bash
# Print the dated feature directory for a short name: specs/YYYY-MM-DD-<kebab-name>.
# A name already used in any spec status folder gets -2, -3, ... appended. Creates nothing.
set -euo pipefail

if [ $# -lt 1 ] || [ -z "${1// /}" ]; then
    echo "usage: $0 <short-name>" >&2
    exit 1
fi

root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
today="${SPECIFY_DATE:-$(date +%Y-%m-%d)}"
name="$(printf '%s' "$*" | LC_ALL=C tr '[:upper:]' '[:lower:]' | LC_ALL=C sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"
if [ -z "$name" ]; then
    echo "error: short name '$*' has no letters or digits" >&2
    exit 1
fi

taken() {
    local candidate="$1" folder
    for folder in specs specs/done specs/superseded; do
        [ -e "$root/$folder/$candidate" ] && return 0
    done
    return 1
}

candidate="$today-$name"
suffix=2
while taken "$candidate"; do
    candidate="$today-$name-$suffix"
    suffix=$((suffix + 1))
done
printf 'SPECIFY_FEATURE_DIRECTORY=specs/%s\n' "$candidate"
