#!/usr/bin/env bash

# Generate the example report images: a cropped teaser for the README and one
# full-page image per page for the docs site. Both are WebP: these pages are
# grey antialiased text, which PNG stores several times less compactly.
# Requires: ImageMagick (with Ghostscript).
# Usage: run with no arguments.

set -euo pipefail

### --- Configuration ---------------------------------------------------------

# Source PDF and teaser image paths
PDF_PATH="docs/assets/example_report.pdf"
OUTPUT_PATH="docs/assets/example_report_preview.webp"

# Rendering resolution (DPI)
DPI=300

# Height (in pixels) of the top strip to keep after rendering.
# Width is taken as full page width automatically. Page 1 is the summary;
# this keeps all of it and stops above the footer, and the trim then drops
# the blank space below the last row.
CROP_HEIGHT=2800

# Margin (in pixels) to add after trimming.
BORDER=20

# Resize after cropping, and the quality to encode the teaser with.
FINAL_WIDTH=1200
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
echo "Density: ${DPI} DPI, Crop: full-width x ${CROP_HEIGHT}, Trim: on"

# Pipeline:
# 1) Render first page at DPI
# 2) Crop full width, top CROP_HEIGHT pixels
# 3) Trim uniform border
# 4) Add optional white border margin
# 5) Flatten onto white to remove transparency
# 6) Resize to look good in README
# 7) Write the WebP
magick -density "$DPI" "$PDF_PATH[0]" -units PixelsPerInch -strip miff:- |
    magick miff:- \
        -gravity North -crop "x${CROP_HEIGHT}+0+0" +repage \
        -trim +repage \
        -bordercolor white -border "${BORDER}" \
        -background white -alpha remove -alpha off \
        -resize "${FINAL_WIDTH}" \
        -quality "${QUALITY}" \
        -strip "$OUTPUT_PATH"

echo "✅ Preview generated: $OUTPUT_PATH"
echo "👀 Check if it looks good or adjust CROP_HEIGHT in the script."

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
