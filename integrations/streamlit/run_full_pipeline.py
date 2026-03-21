#!/usr/bin/env python3
"""
Full pipeline for Streamlit custom integration.

This script runs the complete pipeline:
1. Parse Streamlit app and generate nodes
2. Upload nodes to Paradime

Usage:
    python run_full_pipeline.py
    
Environment Variables:
    GITHUB_TOKEN: Required for accessing private GitHub repositories
    PARADIME_API_ENDPOINT: Paradime API endpoint
    PARADIME_API_KEY: Paradime API key
    PARADIME_API_SECRET: Paradime API secret
"""

import os
import sys
import subprocess
from pathlib import Path


def check_environment():
    """Check if required environment variables are set."""
    required_vars = {
        "GITHUB_TOKEN": "GitHub Personal Access Token",
        "PARADIME_API_ENDPOINT": "Paradime API endpoint",
        "PARADIME_API_KEY": "Paradime API key",
        "PARADIME_API_SECRET": "Paradime API secret",
    }
    
    missing_vars = []
    for var, description in required_vars.items():
        if not os.getenv(var):
            missing_vars.append(f"  - {var}: {description}")
    
    if missing_vars:
        print("ERROR: Missing required environment variables:")
        print("\n".join(missing_vars))
        print("\nPlease set them with:")
        print('  export GITHUB_TOKEN="ghp_your_token_here"')
        print('  export PARADIME_API_ENDPOINT="https://api.paradime.io"')
        print('  export PARADIME_API_KEY="your_key"')
        print('  export PARADIME_API_SECRET="your_secret"')
        return False
    
    print("✓ All required environment variables are set")
    return True


def run_step(step_name: str, script_path: Path) -> bool:
    """Run a pipeline step."""
    print("\n" + "=" * 60)
    print(f"STEP: {step_name}")
    print("=" * 60 + "\n")
    
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=script_path.parent,
            check=True,
            text=True
        )
        print(f"\n✓ {step_name} completed successfully!")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"\n✗ {step_name} failed with exit code {e.returncode}")
        return False
    except Exception as e:
        print(f"\n✗ {step_name} failed: {e}")
        return False


def main():
    """Main entry point."""
    print("=" * 60)
    print("Paradime Custom Integration - Full Pipeline")
    print("Streamlit → Parse → Upload to Paradime")
    print("=" * 60)
    
    # Check environment
    if not check_environment():
        sys.exit(1)
    
    script_dir = Path(__file__).parent
    
    # Step 1: Parse Streamlit app
    if not run_step("Parse Streamlit App", script_dir / "parse.py"):
        print("\n✗ Pipeline failed at parsing step")
        sys.exit(1)
    
    # Step 2: Upload to Paradime
    if not run_step("Upload to Paradime", script_dir / "upload_to_paradime.py"):
        print("\n✗ Pipeline failed at upload step")
        sys.exit(1)
    
    # Success!
    print("\n" + "=" * 60)
    print("✓ FULL PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 60)
    print("\nYour Streamlit integration is now live in Paradime!")
    print("Check your Paradime workspace to see the lineage.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nPipeline cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        sys.exit(1)

