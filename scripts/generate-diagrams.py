#!/usr/bin/env python3
"""Generate SVG diagrams from mermaid source files."""

import subprocess
import sys
from pathlib import Path


def check_mmdc_available() -> tuple[bool, str]:
    """Check if mmdc is available (local or Docker)."""
    # Check for local mmdc installation
    try:
        subprocess.run(["mmdc", "--version"], capture_output=True, check=True)
        return True, "local"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Check for Docker
    try:
        subprocess.run(["docker", "--version"], capture_output=True, check=True)
        return True, "docker"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False, "none"


def generate_svg_local(mmd_file: Path, output_file: Path) -> bool:
    """Generate SVG using local mmdc installation."""
    try:
        subprocess.run(
            ["mmdc", "-i", str(mmd_file), "-o", str(output_file), "-t", "neutral", "-b", "transparent"],
            capture_output=True,
            text=True,
            check=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to generate {output_file.name}: {e.stderr}", file=sys.stderr)
        return False


def generate_svg_docker(mmd_file: Path, output_file: Path) -> bool:
    """Generate SVG using mermaid-cli Docker image."""
    try:
        # Use absolute paths for Docker volume mounts
        mmd_abs = mmd_file.absolute()
        output_abs = output_file.absolute()

        # Get current user ID to avoid permission issues
        import os
        uid = os.getuid()
        gid = os.getgid()

        subprocess.run(
            [
                "docker", "run", "--rm",
                "--user", f"{uid}:{gid}",
                "-v", f"{mmd_abs.parent}:/data",
                "minlag/mermaid-cli",
                "-i", f"/data/{mmd_abs.name}",
                "-o", f"/data/{output_abs.name}",
                "-t", "neutral",
                "-b", "transparent"
            ],
            capture_output=True,
            text=True,
            check=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to generate {output_file.name}: {e.stderr}", file=sys.stderr)
        return False


def generate_svg(mmd_file: Path, output_file: Path, method: str) -> bool:
    """Generate SVG from a mermaid .mmd file."""
    if method == "local":
        success = generate_svg_local(mmd_file, output_file)
    elif method == "docker":
        success = generate_svg_docker(mmd_file, output_file)
    else:
        print("Error: No mermaid-cli available. Install with:", file=sys.stderr)
        print("  npm install -g @mermaid-js/mermaid-cli", file=sys.stderr)
        print("Or use Docker (already available on this system)", file=sys.stderr)
        sys.exit(1)

    if success:
        print(f"✓ Generated: {output_file.name}")
    return success


def main():
    """Generate all SVG diagrams from .mmd files in docs/images/."""
    repo_root = Path(__file__).parent.parent
    images_dir = repo_root / "docs" / "images"

    # Check for mermaid-cli availability
    available, method = check_mmdc_available()
    if not available:
        print("Error: No mermaid-cli available. Install with:", file=sys.stderr)
        print("  npm install -g @mermaid-js/mermaid-cli", file=sys.stderr)
        print("Or ensure Docker is installed", file=sys.stderr)
        return 1

    print(f"Using mermaid-cli via: {method}\n")

    # Find all .mmd files
    mmd_files = sorted(images_dir.glob("*.mmd"))

    if not mmd_files:
        print(f"No .mmd files found in {images_dir}")
        return 0

    print(f"Found {len(mmd_files)} mermaid diagram(s) to generate:\n")

    # Generate SVG for each .mmd file
    success_count = 0
    for mmd_file in mmd_files:
        svg_file = mmd_file.with_suffix(".svg")
        if generate_svg(mmd_file, svg_file, method):
            success_count += 1

    print(f"\n{success_count}/{len(mmd_files)} diagram(s) generated successfully")

    if success_count < len(mmd_files):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
