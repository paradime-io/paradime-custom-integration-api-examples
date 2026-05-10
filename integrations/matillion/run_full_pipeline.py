#!/usr/bin/env python3
"""
Full pipeline for the Matillion custom integration.

Runs the complete pipeline:
  1. Parse Matillion orchestration files and generate nodes.
  2. Upload nodes to Paradime.

Usage:
    python run_full_pipeline.py

Environment Variables:
    GITHUB_TOKEN          Required for accessing private GitHub repositories.
    PARADIME_API_ENDPOINT Paradime API endpoint.
    PARADIME_API_KEY      Paradime API key.
    PARADIME_API_SECRET   Paradime API secret.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def check_environment() -> bool:
    """Check that all required environment variables are present."""
    required_vars = {
        "PARADIME_API_ENDPOINT": "Paradime API endpoint",
        "PARADIME_API_KEY": "Paradime API key",
        "PARADIME_API_SECRET": "Paradime API secret",
    }
    if not os.getenv("MATILLION_LOCAL_DIR") and not os.getenv("MATILLION_LOCAL_FILE"):
        required_vars["GITHUB_TOKEN"] = "GitHub Personal Access Token"

    missing: list[str] = []
    for var, description in required_vars.items():
        if not os.getenv(var):
            missing.append(f"  - {var}: {description}")

    if missing:
        print("ERROR: Missing required environment variables:")
        print("\n".join(missing))
        print("\nPlease set them, for example:")
        print('  export GITHUB_TOKEN="ghp_your_token_here"')
        print('  export PARADIME_API_ENDPOINT="https://api.paradime.io"')
        print('  export PARADIME_API_KEY="your_key"')
        print('  export PARADIME_API_SECRET="your_secret"')
        return False

    print("✓ All required environment variables are set")
    return True


def run_step(step_name: str, script_path: Path) -> bool:
    """Run a single pipeline step."""
    print("\n" + "=" * 60)
    print(f"STEP: {step_name}")
    print("=" * 60 + "\n")

    try:
        subprocess.run(
            [sys.executable, str(script_path)],
            cwd=script_path.parent,
            check=True,
            text=True,
        )
        print(f"\n✓ {step_name} completed successfully!")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"\n✗ {step_name} failed with exit code {exc.returncode}")
        return False
    except Exception as exc:
        print(f"\n✗ {step_name} failed: {exc}")
        return False


def main() -> None:
    print("=" * 60)
    print("Paradime Custom Integration – Full Pipeline")
    print("Matillion → Parse → Upload to Paradime")
    print("=" * 60)

    if not check_environment():
        sys.exit(1)

    script_dir = Path(__file__).parent

    # Step 1: Parse Matillion pipelines
    if not run_step("Parse Matillion Pipelines", script_dir / "parse.py"):
        print("\n✗ Pipeline failed at parsing step.")
        sys.exit(1)

    # Step 2: Upload to Paradime
    if not run_step("Upload to Paradime", script_dir / "upload_to_paradime.py"):
        print("\n✗ Pipeline failed at upload step.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("✓ FULL PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 60)
    print("\nYour Matillion integration is now live in Paradime!")
    print("Check your Paradime workspace to see the lineage.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nPipeline cancelled by user.")
        sys.exit(1)
    except Exception as exc:
        print(f"\n✗ Unexpected error: {exc}")
        sys.exit(1)
