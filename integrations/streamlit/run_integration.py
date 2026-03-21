#!/usr/bin/env python3
"""
Runner script for the Streamlit custom integration.

This script executes the integration pipeline to parse Streamlit apps
and generate Paradime SDK-compatible nodes for lineage tracking.

Usage:
    python run_integration.py
    
Environment Variables:
    GITHUB_TOKEN: Required for accessing private GitHub repositories
"""

import os
import sys
import subprocess
from pathlib import Path


def check_environment():
    """Check if required environment variables are set."""
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("ERROR: GITHUB_TOKEN environment variable is not set")
        print("\nPlease set it with:")
        print('  export GITHUB_TOKEN="ghp_your_token_here"')
        print("\nOr run with:")
        print('  GITHUB_TOKEN="ghp_your_token_here" python run_integration.py')
        sys.exit(1)
    
    print("✓ GITHUB_TOKEN is set")
    return True


def run_integration():
    """Run the integration parse script."""
    script_dir = Path(__file__).parent
    parse_script = script_dir / "parse.py"
    
    if not parse_script.exists():
        print(f"ERROR: parse.py not found at {parse_script}")
        sys.exit(1)
    
    print(f"\nRunning integration from: {script_dir}")
    print("=" * 60)
    
    try:
        # Run the parse.py script
        result = subprocess.run(
            [sys.executable, str(parse_script)],
            cwd=script_dir,
            check=True,
            text=True
        )
        
        print("=" * 60)
        print("\n✓ Integration completed successfully!")
        print(f"✓ Output written to: {script_dir / 'nodes.json'}")
        
        return result.returncode
        
    except subprocess.CalledProcessError as e:
        print("=" * 60)
        print(f"\n✗ Integration failed with exit code {e.returncode}")
        sys.exit(e.returncode)
    except Exception as e:
        print("=" * 60)
        print(f"\n✗ Unexpected error: {e}")
        sys.exit(1)


def main():
    """Main entry point."""
    print("Paradime Custom Integration - Streamlit Parser")
    print("=" * 60)
    
    # Check environment
    check_environment()
    
    # Run the integration
    return run_integration()


if __name__ == "__main__":
    sys.exit(main())
