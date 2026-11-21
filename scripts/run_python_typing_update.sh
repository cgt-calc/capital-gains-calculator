#!/usr/bin/env bash

# Run python-typing-update over the given files, as a commit hook.
# The tool refuses to run while a .py file differs from the last commit,
# which during a commit the staged files always do, so that check is turned
# off. It is there because the tool puts back the files it leaves alone with
# `git restore`, which copies them from the index: an unstaged change to one
# of them would be lost. A commit is safe, as prek sets unstaged changes
# aside first; a run by hand is refused if any of the files has one.
# Usage: scripts/run_python_typing_update.sh [OPTION...] FILE...

set -euo pipefail

files=()
for arg in "$@"; do
    if [[ $arg != -* ]]; then
        files+=("$arg")
    fi
done

if [ "${#files[@]}" -gt 0 ] && ! git diff --quiet -- "${files[@]}"; then
    echo "These files have unstaged changes, which python-typing-update would discard:" >&2
    git diff --name-only -- "${files[@]}" >&2
    echo "Stage or stash them, then run it again." >&2
    exit 1
fi

exec python-typing-update --disable-committed-check "$@"
