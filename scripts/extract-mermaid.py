#!/usr/bin/env python3
"""Extract mermaid diagrams from markdown files and save as .mmd files."""

import re
import sys
from pathlib import Path

def extract_mermaid_diagrams(md_file: Path, output_dir: Path):
    """Extract all mermaid code blocks from a markdown file."""
    content = md_file.read_text()

    # Find all mermaid code blocks
    pattern = r'```mermaid\n(.*?)\n```'
    matches = re.findall(pattern, content, re.DOTALL)

    if not matches:
        return 0

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save each diagram
    base_name = md_file.stem
    for i, diagram in enumerate(matches, 1):
        suffix = f"-{i}" if len(matches) > 1 else ""
        output_file = output_dir / f"{base_name}{suffix}.mmd"
        output_file.write_text(diagram.strip() + "\n")
        print(f"Extracted: {output_file}")

    return len(matches)

def main():
    repo_root = Path(__file__).parent.parent
    docs_dir = repo_root / "docs"
    images_dir = docs_dir / "images"

    total = 0
    for md_file in docs_dir.glob("*.md"):
        count = extract_mermaid_diagrams(md_file, images_dir)
        if count > 0:
            print(f"  {md_file.name}: {count} diagram(s)")
            total += count

    print(f"\nTotal: {total} mermaid diagram(s) extracted to {images_dir}")

if __name__ == "__main__":
    main()
