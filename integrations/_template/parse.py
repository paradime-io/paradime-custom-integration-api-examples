"""
Paradime Custom Integration – [YOUR TOOL NAME].

Replace every occurrence of [YOUR TOOL NAME] / [YOUR_ENV_PREFIX] / [your_tool]
with your actual tool name before running.

This script:
  1. Downloads your source repository from GitHub (as a ZIP archive).
  2. Parses the relevant files.
  3. Converts the parsed data into Paradime SDK node dictionaries.
  4. Saves the result to ``<repo_root>/target/[your_tool]_nodes.json``.

Node model
----------
  ParentNode – one per [unit of work], e.g. one per pipeline file.
  ChildNode  – one per [sub-unit],     e.g. one per job inside the pipeline.

Lineage (choose the direction that matches your tool)
------------------------------------------------------
  Option A (tool feeds dbt):
    [YourTool] ParentNode → ChildNode → Snowflake table ← dbt source

  Option B (dbt feeds tool):
    dbt model → ParentNode → ChildNode

Usage:
    python parse.py

Environment Variables:
    [YOUR_ENV_PREFIX]_REPO_URL   GitHub repo URL
    [YOUR_ENV_PREFIX]_BRANCH     Branch to download (default: main)
    GITHUB_TOKEN                 GitHub PAT (required for private repos)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Path setup – allow importing from src/ (add your own parser under src/parsers/)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
# from src.parsers.[your_tool].parser import YourParser  # ← uncomment when ready

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------

def _find_repo_root(start: Path) -> Path:
    """
    Walk up the directory tree from ``start`` to find the repository root.

    The root is identified by the presence of ``dbt_project.yml``.
    Falls back to the current working directory if not found.

    Args:
        start: Directory to begin searching from.

    Returns:
        Resolved Path to the repository root.
    """
    current = start.resolve()
    while True:
        if (current / "dbt_project.yml").exists():
            return current
        parent = current.parent
        if parent == current:
            logger.warning(
                "Could not locate dbt_project.yml — "
                "falling back to current working directory for output."
            )
            return Path.cwd()
        current = parent


# ---------------------------------------------------------------------------
# Configuration – all overridable via environment variables
# ---------------------------------------------------------------------------

# TODO: update the defaults to point at your own demo / sandbox repo
REPO_URL = os.getenv(
    "[YOUR_ENV_PREFIX]_REPO_URL",
    "https://github.com/your-org/your-repo",
)
BRANCH = os.getenv("[YOUR_ENV_PREFIX]_BRANCH", "main")

# Optional comma-separated filter — leave unset to process every file
_raw_filter = os.getenv("[YOUR_ENV_PREFIX]_FILE_FILTER", "").strip()
FILE_FILTER: list[str] | None = (
    [p.strip() for p in _raw_filter.split(",") if p.strip()]
    if _raw_filter
    else None
)


# ---------------------------------------------------------------------------
# GitHub helpers  (copy-paste — do not modify)
# ---------------------------------------------------------------------------

def download_repo(
    repo_url: str,
    target_path: Path,
    github_token: str | None = None,
    branch: str = "main",
) -> bool:
    """
    Download a GitHub repository as a ZIP archive and extract it locally.

    Args:
        repo_url:     GitHub repository URL.
        target_path:  Local directory to extract into.
        github_token: Optional GitHub PAT for private repos.
        branch:       Branch to download.

    Returns:
        True on success, False on failure.
    """
    if target_path.exists():
        shutil.rmtree(target_path)

    repo_url = repo_url.rstrip("/").removesuffix(".git")
    zip_url = f"{repo_url}/archive/refs/heads/{branch}.zip"
    logger.info(f"Downloading repository from {zip_url}")

    request = Request(zip_url)
    if github_token:
        request.add_header("Authorization", f"token {github_token}")

    try:
        with urlopen(request) as response:
            zip_data = BytesIO(response.read())
        logger.info(f"Downloaded {len(zip_data.getvalue()):,} bytes")
    except HTTPError as exc:
        logger.error(f"HTTP {exc.code}: {exc.reason}")
        if exc.code == 404:
            logger.error("Repository or branch not found.")
        elif exc.code in (401, 403):
            logger.error("Authentication failed — check GITHUB_TOKEN.")
        return False
    except URLError as exc:
        logger.error(f"Network error: {exc.reason}")
        return False

    try:
        with zipfile.ZipFile(zip_data) as zf:
            zf.extractall(target_path.parent)

        repo_name = repo_url.split("/")[-1]
        extracted = target_path.parent / f"{repo_name}-{branch}"
        if extracted.exists():
            extracted.rename(target_path)
        else:
            logger.error(f"Expected extracted folder not found: {extracted}")
            return False
    except Exception as exc:
        logger.error(f"Failed to extract ZIP: {exc}")
        return False

    logger.info(f"Repository extracted to: {target_path}")
    return True


# ---------------------------------------------------------------------------
# TODO: implement your parser
# ---------------------------------------------------------------------------

def parse_source_file(file_path: Path) -> list[dict[str, Any]]:
    """
    Parse a single source file and return a list of raw records.

    Replace this stub with your actual parsing logic.

    Args:
        file_path: Path to the file to parse.

    Returns:
        List of raw record dictionaries extracted from the file.
    """
    # Example stub — replace with real parsing:
    logger.info(f"Parsing: {file_path}")
    records: list[dict[str, Any]] = []

    # e.g. parse YAML / JSON / Python AST and populate records
    # records.append({"name": "my_job", "table": "MY_TABLE", ...})

    return records


# ---------------------------------------------------------------------------
# TODO: implement node conversion
# ---------------------------------------------------------------------------

def convert_to_paradime_nodes(
    records: list[dict[str, Any]],
    source_name: str,
    file_url: str,
) -> list[dict[str, Any]]:
    """
    Convert parsed records into Paradime SDK node dictionaries.

    Each node must have the shape:
    {
        "name":      str,            # unique within the integration
        "node_type": str,            # must match a type in node_types.json
        "attributes": {
            "description": str,
            "url":         str,      # link to the source in GitHub
        },
        "lineage": {
            "upstream_dependencies":   [...],  # see options below
            "downstream_dependencies": [...],  # see options below
        }
    }

    Dependency shapes
    -----------------
    Reference another node IN this integration:
        {"integration_name": "YourIntegrationName", "node_type": "ParentNode", "node_name": "my_parent"}

    Reference a dbt table (creates lineage with dbt models):
        {"table_name": "my_snowflake_table_name"}  # lower-cased, no schema prefix

    Args:
        records:     Raw records from parse_source_file().
        source_name: Display name of the parent entity (e.g. pipeline name).
        file_url:    GitHub URL to the source file (used for the node URL).

    Returns:
        List of Paradime node dicts.
    """
    nodes: list[dict[str, Any]] = []

    # ── Example: one ParentNode per file ───────────────────────────────────
    nodes.append(
        {
            "name": source_name,
            "node_type": "ParentNode",
            "attributes": {
                "description": f"Auto-generated parent node for {source_name}.",
                "url": file_url,
            },
            "lineage": {
                "upstream_dependencies": [],
                # downstream: nothing — ParentNode is the top-level container
            },
        }
    )

    # ── Example: one ChildNode per record ──────────────────────────────────
    for record in records:
        child_name = record.get("name", "unknown")
        table_name = record.get("table", "")

        downstream: list[dict[str, Any]] = []
        if table_name:
            # Option A: this child writes to a Snowflake table that dbt reads
            downstream.append({"table_name": table_name.lower()})

        nodes.append(
            {
                "name": f"{source_name}.{child_name}",
                "node_type": "ChildNode",
                "attributes": {
                    "description": f"Child node: {child_name}.",
                    "url": file_url,
                },
                "lineage": {
                    "upstream_dependencies": [
                        {
                            "integration_name": "Your Integration Name",
                            "node_type": "ParentNode",
                            "node_name": source_name,
                        }
                    ],
                    "downstream_dependencies": downstream,
                },
            }
        )

    return nodes


# ---------------------------------------------------------------------------
# Main extraction pipeline  (structure — do not modify)
# ---------------------------------------------------------------------------

def extract_and_save_nodes(
    repo_url: str,
    branch: str,
    github_token: str | None,
    file_filter: list[str] | None = None,
) -> None:
    """
    Full extraction pipeline:
      1. Download the repository.
      2. Locate source files (all, or those in ``file_filter``).
      3. Parse each file.
      4. Convert to Paradime nodes.
      5. Write ``<repo_root>/target/[your_tool]_nodes.json``.

    Args:
        repo_url:     GitHub repository URL.
        branch:       Git branch to download.
        github_token: Optional GitHub PAT for private repos.
        file_filter:  Optional list of repo-relative file paths.
    """
    script_dir = Path(__file__).resolve().parent
    temp_dir = script_dir / "temp_repo"

    # Resolve output path — always goes into <repo_root>/target/
    repo_root = _find_repo_root(script_dir)
    target_dir = repo_root / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    # TODO: rename the output file to match your integration
    output_file = target_dir / "[your_tool]_nodes.json"

    logger.info(f"Output will be written to: {output_file}")

    # Step 1 — download
    if not download_repo(repo_url, temp_dir, github_token, branch):
        logger.error("Failed to download repository. Aborting.")
        sys.exit(1)

    # Step 2 — find files
    # TODO: replace "*.your_extension" with the glob that matches your source files
    if file_filter:
        source_files: list[Path] = []
        for relative_path in file_filter:
            candidate = temp_dir / relative_path
            if candidate.exists():
                source_files.append(candidate)
            else:
                logger.warning(f"Filtered file not found — skipping: '{relative_path}'")
        if not source_files:
            logger.error("None of the filtered files were found in the repo.")
            sys.exit(1)
    else:
        source_files = list(temp_dir.glob("**/*.your_extension"))
        if not source_files:
            logger.error("No source files found in the repository.")
            sys.exit(1)

    logger.info(f"Processing {len(source_files)} file(s)")

    # Steps 3 & 4 — parse and convert (try/finally guarantees cleanup)
    all_nodes: list[dict[str, Any]] = []
    try:
        for source_file in source_files:
            relative_path = str(source_file.relative_to(temp_dir))
            file_url = f"{repo_url}/blob/{branch}/{relative_path}"

            # Derive a human-friendly name from the filename
            source_name = (
                source_file.stem.replace("_", " ").title()
            )

            records = parse_source_file(source_file)
            nodes = convert_to_paradime_nodes(
                records=records,
                source_name=source_name,
                file_url=file_url,
            )
            logger.info(f"  '{source_file.name}' → {len(nodes)} node(s)")
            all_nodes.extend(nodes)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            logger.info("Cleanup complete — temp_repo removed.")

    # Step 5 — write output
    output_file.write_text(json.dumps(all_nodes, indent=2), encoding="utf-8")
    logger.info(f"Saved {len(all_nodes)} total nodes to {output_file}")


if __name__ == "__main__":
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    if not GITHUB_TOKEN:
        logger.warning(
            "GITHUB_TOKEN not set — attempting anonymous download. "
            "This will fail for private repositories."
        )

    extract_and_save_nodes(
        repo_url=REPO_URL,
        branch=BRANCH,
        github_token=GITHUB_TOKEN,
        file_filter=FILE_FILTER,
    )
