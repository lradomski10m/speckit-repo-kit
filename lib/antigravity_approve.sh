#!/bin/sh
# Antigravity hook launcher written by speckit-repo-kit: runs approve.py, beside it, with the first
# Python 3.9+ there is (python3, python, then Windows' launcher py -3), passing the tool call on
# stdin through. Without one it leaves the call to Antigravity's own rules ("allow").
#
# .agents/hooks.json runs it through a git alias, `git -c "alias.repo-kit-approve=!sh <this file>"
# repo-kit-approve <group>`, so the same committed line works whatever shell Antigravity uses (sh on
# macOS and Linux; cmd or PowerShell on Windows, where Git for Windows brings its own sh) and from
# whatever folder it starts in: git runs the alias at the top of the checkout the session is in.
# Written by the kit; change repo-kit.json and rerun the kit's bootstrap.sh rather than this file.
here=$(dirname "$0")
for python in python3 python "py -3"; do
    # The check skips what isn't a usable Python, such as Windows' python3 stand-in that only offers
    # to install one from the Store.
    if $python -c 'import sys; sys.exit(sys.version_info < (3, 9))' </dev/null >/dev/null 2>&1; then
        exec $python -I "$here/approve.py" "$@"
    fi
done
echo '{"decision": "allow"}'
