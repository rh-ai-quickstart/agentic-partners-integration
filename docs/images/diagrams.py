#!/usr/bin/env python3
"""
Mermaid diagram tool: Extract from markdown and generate SVG files.
Works on current directory - run from anywhere.

Usage:
    cd docs/images/
    python diagrams.py                # Extract .md → .mmd AND generate .mmd → .svg
    python diagrams.py extract        # Only extract .md → .mmd
    python diagrams.py generate       # Only generate .mmd → .svg
"""

import re
import subprocess
import sys
from pathlib import Path


# =============================================================================
# EXTRACT: .md → .mmd
# =============================================================================

def extract_mermaid_diagrams(md_file: Path) -> int:
    """Extract all mermaid code blocks from a markdown file."""
    content = md_file.read_text()
    pattern = r'```mermaid\n(.*?)\n```'
    matches = re.findall(pattern, content, re.DOTALL)

    if not matches:
        return 0

    base_name = md_file.stem
    for i, diagram in enumerate(matches, 1):
        suffix = f"-{i}" if len(matches) > 1 else ""
        output_file = md_file.parent / f"{base_name}{suffix}.mmd"
        output_file.write_text(diagram.strip() + "\n")
        print(f"  ✓ {output_file.name}")

    return len(matches)


def extract_all() -> int:
    """Extract mermaid diagrams from all .md files in current directory."""
    current_dir = Path.cwd()
    print(f"Extracting from: {current_dir}")
    print()

    md_files = sorted(current_dir.glob("*.md"))
    if not md_files:
        print("No .md files found")
        return 0

    total = 0
    for md_file in md_files:
        count = extract_mermaid_diagrams(md_file)
        if count > 0:
            print(f"{md_file.name}: {count} diagram(s)")
            total += count

    print()
    print(f"✓ Extracted {total} diagram(s)")
    return total


# =============================================================================
# GENERATE: .mmd → .svg
# =============================================================================

def check_mmdc() -> tuple[bool, str]:
    """Check if mermaid-cli is available (local or Docker)."""
    try:
        subprocess.run(["mmdc", "--version"], capture_output=True, check=True)
        return True, "local"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    try:
        subprocess.run(["docker", "--version"], capture_output=True, check=True)
        return True, "docker"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False, "none"


def generate_svg(mmd_file: Path, method: str) -> bool:
    """Generate SVG from .mmd file."""
    svg_file = mmd_file.with_suffix(".svg")

    if method == "local":
        try:
            subprocess.run(
                ["mmdc", "-i", str(mmd_file), "-o", str(svg_file), "-t", "neutral", "-b", "transparent"],
                capture_output=True, check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    elif method == "docker":
        try:
            import os
            uid, gid = os.getuid(), os.getgid()
            mmd_abs = mmd_file.absolute()

            subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--user", f"{uid}:{gid}",
                    "-v", f"{mmd_abs.parent}:/data",
                    "minlag/mermaid-cli",
                    "-i", f"/data/{mmd_abs.name}",
                    "-o", f"/data/{svg_file.name}",
                    "-t", "neutral",
                    "-b", "transparent"
                ],
                capture_output=True, check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    return False


def generate_all() -> int:
    """Generate SVG from all .mmd files in current directory."""
    current_dir = Path.cwd()
    print(f"Generating SVGs from: {current_dir}")
    print()

    available, method = check_mmdc()
    if not available:
        print("Error: No mermaid-cli available", file=sys.stderr)
        print("Install: npm install -g @mermaid-js/mermaid-cli", file=sys.stderr)
        print("Or ensure Docker is installed", file=sys.stderr)
        return 0

    print(f"Using: {method}")
    print()

    mmd_files = sorted(current_dir.glob("*.mmd"))
    if not mmd_files:
        print("No .mmd files found")
        return 0

    success = 0
    for mmd_file in mmd_files:
        if generate_svg(mmd_file, method):
            print(f"  ✓ {mmd_file.name} → {mmd_file.stem}.svg")
            success += 1

    print()
    print(f"✓ Generated {success}/{len(mmd_files)} diagram(s)")
    return success


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main entry point."""
    command = sys.argv[1] if len(sys.argv) > 1 else "all"

    if command in ["extract", "e"]:
        extract_all()

    elif command in ["generate", "gen", "g"]:
        generate_all()

    elif command in ["all", "both", "a"]:
        extracted = extract_all()
        if extracted > 0:
            print()
            generate_all()

    else:
        print(f"Unknown command: {command}")
        print("\nUsage:")
        print("  python diagrams.py           # Extract & generate (default)")
        print("  python diagrams.py extract   # Only extract .md → .mmd")
        print("  python diagrams.py generate  # Only generate .mmd → .svg")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
