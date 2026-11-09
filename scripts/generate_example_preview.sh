#!/usr/bin/env bash

# Generate the example report images: a two-page teaser for the README and one
# full-page image per page for the docs site. Both are WebP: these pages are
# grey antialiased text, which PNG stores several times less compactly.
# Requires: ImageMagick (with Ghostscript).
# Usage: run with no arguments.

set -euo pipefail

### --- Configuration ---------------------------------------------------------

# Source PDF and teaser image paths
PDF_PATH="docs/assets/example_report.pdf"
OUTPUT_PATH="docs/assets/example_report_preview.webp"

# The teaser shows page 2 lying on page 1 as sheets of paper: a thin grey edge
# and a soft shadow on a transparent background, so it suits GitHub's light and
# dark themes. Page 1's capital gains summary stays in view above page 2, and
# the picture fades out below page 2's first disposal. The offsets and HEIGHT
# follow the example report's layout, so check the teaser after that changes.
# The README shows it at half size.
DPI=200
TEASER_PAGE_WIDTH=1080 # each page, before its edge and shadow
EDGE_COLOR="#d0d7de"   # GitHub's border grey
EDGE_WIDTH=2
SHADOW="28x14+0+8" # opacity (%) x blur + x offset + y offset
OFFSET_X=64        # page 2's shift right; only page 1's blank margin shows
OFFSET_Y=656       # page 2's shift down; its top edge is below page 1's summary
HEIGHT=1560        # the picture ends just below page 2's first disposal
FADE=45            # its last rows fade to transparent
QUALITY=82

# Full-page images for the docs site: prefix, resolution and width.
PAGE_PREFIX="docs/assets/example_report_page"
PAGE_DPI=200
PAGE_WIDTH=1400
PAGE_QUALITY=82

### ---------------------------------------------------------------------------

# Change to project root
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "Generating preview from '$PDF_PATH' → '$OUTPUT_PATH'"
echo "Density: ${DPI} DPI, Pages: 1-2, Page width: ${TEASER_PAGE_WIDTH}"

# Each page: flatten onto white paper, resize, draw its thin edge, then put its
# own soft shadow behind it. Page 2's shadow falls on page 1.
PAGE_STYLE=(
    -background white -alpha remove -alpha off
    -resize "$TEASER_PAGE_WIDTH"
    -bordercolor "$EDGE_COLOR" -border "$EDGE_WIDTH"
    \( +clone -background black -shadow "$SHADOW" \) +swap
    -background none -layers merge
)

# Style pages 1 and 2, move page 2 by the offsets ("!" adds them to the current
# offset) and lay it over page 1. Keep the top HEIGHT rows, scale the opacity
# of the last FADE rows down to zero, and write the WebP with transparency.
magick -density "$DPI" \
    \( "${PDF_PATH}[0]" "${PAGE_STYLE[@]}" \) \
    \( "${PDF_PATH}[1]" "${PAGE_STYLE[@]}" -repage "+${OFFSET_X}+${OFFSET_Y}!" \) \
    -background none -layers merge +repage \
    -crop "x${HEIGHT}+0+0" +repage \
    -channel A -fx "u*min(1,(h-1-j)/${FADE})" +channel \
    -quality "$QUALITY" \
    -strip "$OUTPUT_PATH"

echo "✅ Preview generated: $OUTPUT_PATH"

echo
echo "Generating full-page images from '$PDF_PATH' → '${PAGE_PREFIX}N.webp'"
echo "Density: ${PAGE_DPI} DPI, Width: ${PAGE_WIDTH}, Quality: ${PAGE_QUALITY}"

# Drop images from a previous run so a shorter report leaves no stale pages behind.
rm -f "${PAGE_PREFIX}"*.webp

# Render every page: flatten onto white, resize to a fixed width, write WebP.
# The docs refer to pages from one, so -scene starts the %d numbering there.
magick -density "$PAGE_DPI" "$PDF_PATH" \
    -background white -alpha remove -alpha off \
    -resize "$PAGE_WIDTH" \
    -quality "$PAGE_QUALITY" \
    -scene 1 \
    -strip "${PAGE_PREFIX}%d.webp"

echo "✅ Page images generated: $(ls "${PAGE_PREFIX}"*.webp | wc -l | tr -d ' ') pages"
