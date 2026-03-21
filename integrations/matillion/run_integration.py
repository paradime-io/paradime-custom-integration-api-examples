#!/usr/bin/env python3
"""
Runner script for the Matillion custom integration.

Executes the parsing pipeline to extract Matillion pipeline metadata
and generate Paradime SDK-compatible nodes for lineage tracking.

Usage:
    python run_integration.py

Environment Variables:
    GITHUB_TOKEN: Required for accessing private GitHub repositories.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def check_environment() -> bool:
    """Check that all required environment variables are present."""
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("ERROR: GITHUB_TOKEN environment variable is not set.")
        print("\nPlease set it with:")
        print('  export GITHUB_TOKEN="ghp_your_token_here"')
        print("\nOr run inline:")
        print('  GITHUB_TOKEN="ghp_your_token_here" python run_integration.py')
        sys.exit(1)

    print("✓ GITHUB_TOKEN is set")
    return True


def run_integration() -> int:
    """Run the parse.py script."""
    script_dir = Path(__file__).parent
    parse_script = script_dir / "parse.py"

    if not parse_script.exists():
        print(f"ERROR: parse.py not found at {parse_script}")
        sys.exit(1)

    print(f"\nRunning integration from: {script_dir}")
    print("=" * 60)

    try:
        result = subprocess.run(
            [sys.executable, str(parse_script)],
            cwd=script_dir,
            check=True,
            text=True,
        )
        print("=" * 60)
        print("\n✓ Integration completed successfully!")
        print("✓ Output written to: target/matillion_nodes.json")
        return result.returncode

    except subprocess.CalledProcessError as exc:
        print("=" * 60)
        print(f"\n✗ Integration failed with exit code {exc.returncode}")
        sys.exit(exc.returncode)
    except Exception as exc:
        print("=" * 60)
        print(f"\n✗ Unexpected error: {exc}")
        sys.exit(1)


def main() -> None:
    print("Paradime Custom Integration – Matillion Parser")
    print("=" * 60)
    check_environment()
    sys.exit(run_integration())


if __name__ == "__main__":
    main()
