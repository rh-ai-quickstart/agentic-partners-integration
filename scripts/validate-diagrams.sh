#!/bin/bash
# Validate diagram sources: ensure every .mmd has a corresponding .svg
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIAGRAM_DIR="$(dirname "$SCRIPT_DIR")/docs/images"

errors=0

for mmd in "$DIAGRAM_DIR"/*.mmd; do
    base="$(basename "$mmd" .mmd)"
    svg="$DIAGRAM_DIR/${base}.svg"
    if [ ! -f "$svg" ]; then
        echo "MISSING SVG: $base.mmd has no matching $base.svg"
        errors=$((errors + 1))
    fi
done

for svg in "$DIAGRAM_DIR"/*.svg; do
    [ -f "$svg" ] || continue
    if ! grep -q '<svg' "$svg" 2>/dev/null; then
        echo "INVALID SVG: $(basename "$svg") is not a valid SVG file"
        errors=$((errors + 1))
    fi
done

if [ "$errors" -gt 0 ]; then
    echo ""
    echo "Found $errors diagram issue(s)"
    exit 1
fi

echo "All diagrams validated ($(ls "$DIAGRAM_DIR"/*.mmd 2>/dev/null | wc -l) sources, $(ls "$DIAGRAM_DIR"/*.svg 2>/dev/null | wc -l) SVGs)"
