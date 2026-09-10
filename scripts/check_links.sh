#!/usr/bin/env bash

# Check the links in all Markdown files with lychee.
#
# Two passes are run, the offline one first:
#   offline  - local files and heading anchors only. No network, so it is
#              deterministic and only fails on genuine mistakes in the change.
#   external - everything, including third party sites. Can fail for reasons
#              unrelated to the change under review.
#
# Shared settings live in lychee.toml. The remaps resolve cgt-calc.uk URLs
# against the local docs sources and this repository's own file URLs against
# the checkout, so a link to a page or file added in the same change is checked
# before it reaches main. lychee requires an absolute file:// target for a
# remap, hence the interpolation of the repository root below.
#
# The offline pass also reads the package sources, where the error messages
# print documentation URLs. lychee treats any file it does not recognise as
# plain text, so those are found in strings and comments alike, but only when
# a URL is written contiguously: a literal split across two lines ends at the
# closing quote and loses its anchor. Remote URLs in the sources are excluded
# by --offline, so this pass stays deterministic.
#
# Usage: scripts/check_links.sh [offline|external]

set -uo pipefail

root="$(git rev-parse --show-toplevel)" || exit 1
cd "$root" || exit 1

# Single quotes around the patterns keep the backslash and lychee's own $1
# capture reference literal; only the repository root is interpolated.
remap='https://cgt-calc\.uk/(.*) file://'"$root"'/docs/$1'

# Only blob URLs, which name a file. A tree URL names a directory, which lychee
# resolves through the index_files setting, so remapping one would look for an
# index.md inside the target and report the link as broken.
repo_remap='https://github\.com/cgt-calc/capital-gains-calculator/blob/main/(.*) file://'"$root"'/$1'

lychee=(uv run --only-group links lychee --remap "$remap" --remap "$repo_remap")

status=0

# Run one pass. Under GitHub Actions the report is also appended to the job
# summary, so broken links are visible without opening the log. `--output`
# silences stdout, so the report is echoed to the console as well.
run_pass() {
    local title="$1"
    shift

    echo "==> $title"

    if [ -z "${GITHUB_STEP_SUMMARY:-}" ]; then
        "${lychee[@]}" "$@" || status=1
        return
    fi

    local report rc
    report="$(mktemp)" || return 1
    "${lychee[@]}" --format markdown --output "$report" "$@"
    rc=$?
    cat "$report"
    {
        echo "## $title"
        # Drop lychee's own top level heading so each pass sits under its
        # own heading in the job summary.
        sed -e '1{/^# Summary$/d;}' -e '2{/^$/d;}' "$report"
    } >>"$GITHUB_STEP_SUMMARY"
    rm -f "$report"
    [ "$rc" -eq 0 ] || status=1
}

check_offline() {
    run_pass "Local files and heading anchors" \
        --offline --include-fragments=anchor-only './**/*.md' './cgt_calc/**/*.py'
}

check_external() {
    run_pass "External links" './**/*.md'
}

case "${1:-all}" in
offline) check_offline ;;
external) check_external ;;
all)
    check_offline
    check_external
    ;;
*)
    echo "Usage: $0 [offline|external]" >&2
    exit 2
    ;;
esac

exit "$status"
