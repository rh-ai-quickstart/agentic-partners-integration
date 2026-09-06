#!/bin/bash
# Validate that every SVG diagram has a corresponding .mmd source file
# This script is intended to run in CI to ensure diagram sources are maintained

set -e

IMAGES_DIR="docs/images"
ERRORS=0

# List of SVG files that are hand-crafted (not generated from mermaid)
EXCEPTIONS=(
    "ui-screenshot.svg"
)

echo "Validating diagram sources in $IMAGES_DIR..."
echo ""

# Check if exceptions array contains a value
is_exception() {
    local file="$1"
    for exception in "${EXCEPTIONS[@]}"; do
        if [ "$file" = "$exception" ]; then
            return 0
        fi
    done
    return 1
}

# Find all SVG files and check for corresponding .mmd files
while IFS= read -r svg_file; do
    svg_basename=$(basename "$svg_file")
    mmd_file="${svg_file%.svg}.mmd"

    # Skip if this is an exception (hand-crafted SVG)
    if is_exception "$svg_basename"; then
        echo "⊘ SKIP: $svg_basename (hand-crafted, not generated from mermaid)"
        continue
    fi

    # Check if .mmd source exists
    if [ ! -f "$mmd_file" ]; then
        echo "✗ ERROR: $svg_file has no source file ${mmd_file##*/}"
        ERRORS=$((ERRORS + 1))
    else
        echo "✓ OK: $svg_basename → ${mmd_file##*/}"
    fi
done < <(find "$IMAGES_DIR" -name "*.svg" -type f | sort)

echo ""

# Summary
if [ $ERRORS -eq 0 ]; then
    echo "✓ All SVG diagrams have corresponding .mmd source files"
    exit 0
else
    echo "✗ Found $ERRORS SVG file(s) without .mmd source"
    echo ""
    echo "To fix:"
    echo "  1. Create missing .mmd files in $IMAGES_DIR/"
    echo "  2. Run 'make diagrams' to regenerate SVGs"
    echo "  3. Or add the file to EXCEPTIONS array if it's hand-crafted"
    exit 1
fi
