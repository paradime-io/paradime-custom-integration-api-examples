"""
Paradime Custom Integration for Matillion Orchestration Pipelines.

This script:
  1. Downloads a Matillion pipeline repository from GitHub (as a ZIP archive).
  2. Parses all `.orch.yaml` files found in the repo.
  3. Converts pipelines and their components into Paradime SDK nodes.
  4. Saves the resulting nodes to ``<repo_root>/target/matillion_nodes.json``.

Node model
----------
  Pipeline  – one per `.orch.yaml` file, represents the whole pipeline.
  Job       – one per non-Start component, represents a single load job.

Lineage
-------
  dbt source table (Snowflake table written by Matillion)
    ← Job (writes the Snowflake table)
    ← Pipeline (parent container)

Usage:
    python parse.py

Environment Variables:
    GITHUB_TOKEN            GitHub Personal Access Token (needed for private repos)
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
# Path setup – allow importing from src/
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.parsers.matillion.parser import parse_matillion_yaml

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

    The root is identified by the presence of ``dbt_project.yml``, which is
    always at the repo root in this project.  If not found after traversing
    the full tree, falls back to the current working directory so the script
    never crashes — it just writes to a less ideal location.

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
            # Reached filesystem root without finding the marker
            logger.warning(
                "Could not locate dbt_project.yml — "
                "falling back to current working directory for output."
            )
            return Path.cwd()
        current = parent

# ---------------------------------------------------------------------------
# Configuration – override via environment variables or edit defaults below
# ---------------------------------------------------------------------------
REPO_URL = os.getenv(
    "MATILLION_REPO_URL",
    "https://github.com/paradime-sandbox/matillion-pipelines",
)
BRANCH = os.getenv("MATILLION_BRANCH", "main")

# Optional comma-separated list of .orch.yaml file paths to process.
# Paths are relative to the repo root (e.g. "f1_api_load.orch.yaml" or
# "pipelines/f1_api_load.orch.yaml,pipelines/f1_cc_load.orch.yaml").
# Leave unset (or set to an empty string) to process ALL .orch.yaml files.
#
# Example:
#   MATILLION_PIPELINE_FILTER="f1_api_load.orch.yaml"
#   MATILLION_PIPELINE_FILTER="f1_api_load.orch.yaml,f1_custom_connector_load.orch.yaml"
_raw_filter = os.getenv("MATILLION_PIPELINE_FILTER", "f1_custom_connector_load.orch.yaml").strip()
PIPELINE_FILTER: list[str] | None = (
    [p.strip() for p in _raw_filter.split(",") if p.strip()]
    if _raw_filter
    else None
)


# ---------------------------------------------------------------------------
# GitHub helpers
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
        repo_url:     GitHub repository URL (e.g. https://github.com/org/repo)
        target_path:  Local directory to extract into.
        github_token: Optional GitHub PAT for private repos.
        branch:       Branch to download (default: main).

    Returns:
        True on success, False on failure.
    """
    if target_path.exists():
        logger.info(f"Removing existing temp directory: {target_path}")
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
        logger.error(f"HTTP {exc.code} downloading repo: {exc.reason}")
        if exc.code == 404:
            logger.error("Repository or branch not found. Check the URL and branch name.")
        elif exc.code in (401, 403):
            logger.error("Authentication failed. Check your GITHUB_TOKEN.")
        return False
    except URLError as exc:
        logger.error(f"Network error downloading repo: {exc.reason}")
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
# Node conversion
# ---------------------------------------------------------------------------

def _table_name_to_dbt_model(table_name: str) -> str:
    """
    Convert a Matillion Snowflake output table name to a dbt model name.

    Matillion table names are upper-cased (e.g. F1_DRIVERS_CC).
    We lower-case them to match dbt's convention.

    Args:
        table_name: Raw Snowflake table name from the Matillion YAML.

    Returns:
        Lower-cased model/table name.
    """
    return table_name.lower()


def convert_to_paradime_nodes(
    parsed_pipeline: dict[str, Any],
    repo_url: str,
    yaml_file_path: str,
    branch: str = "main",
) -> list[dict[str, Any]]:
    """
    Convert a parsed Matillion pipeline into a list of Paradime SDK node dicts.

    Args:
        parsed_pipeline: Output of :func:`parse_matillion_yaml`.
        repo_url:        GitHub repo URL (used to build source links).
        yaml_file_path:  Relative path of the YAML file inside the repo.
        branch:          Git branch name.

    Returns:
        List of node dictionaries ready to be serialised into ``nodes.json``.
    """
    nodes: list[dict[str, Any]] = []

    pipeline_name: str = parsed_pipeline["pipeline_name"]
    pipeline_type: str = parsed_pipeline.get("pipeline_type", "unknown")
    description: str = parsed_pipeline["description"]
    components: list[dict[str, Any]] = parsed_pipeline["components"]

    # Source link for the pipeline file
    file_url = f"{repo_url}/blob/{branch}/{yaml_file_path}"

    # ------------------------------------------------------------------
    # 1. Pipeline node  (one per .orch.yaml)
    # ------------------------------------------------------------------
    # Count only non-Start, non-skipped active jobs
    active_jobs = [
        c for c in components
        if c["type"] != "start" and not c["skipped"]
    ]
    skipped_jobs = [
        c for c in components
        if c["type"] != "start" and c["skipped"]
    ]

    type_label = {
        "ingestion": "ingestion (SaaS → Snowflake)",
        "reverse_etl": "reverse ETL (Snowflake → destination)",
    }.get(pipeline_type, "orchestration")

    pipeline_description = description or (
        f"Matillion {type_label} pipeline with {len(active_jobs)} active job(s)"
        + (f" and {len(skipped_jobs)} skipped job(s)" if skipped_jobs else "")
        + "."
    )

    pipeline_node: dict[str, Any] = {
        "name": pipeline_name,
        "node_type": "Pipeline",
        "attributes": {
            "description": pipeline_description,
            "url": file_url,
        },
        "lineage": {
            "upstream_dependencies": []
        },
    }
    nodes.append(pipeline_node)

    # ------------------------------------------------------------------
    # 2. Job nodes  (one per non-Start component)
    # ------------------------------------------------------------------
    for component in components:
        # Skip the Start pseudo-component – it's not a real job
        if component["type"] == "start":
            continue

        comp_name: str = component["name"]
        output_table: str | None = component["output_table"]
        source_table: str | None = component["source_table"]
        endpoint: str | None = component["endpoint"]
        skipped: bool = component["skipped"]

        # Build a readable description
        action_parts = []
        if endpoint:
            action_parts.append(f"Calls the **{endpoint}** API endpoint")
        if pipeline_type == "ingestion" and output_table:
            action_parts.append(f"writes results to Snowflake table `{output_table}`")
        elif pipeline_type == "reverse_etl" and source_table:
            action_parts.append(f"reads from Snowflake table/view `{source_table}`")

        if action_parts:
            job_description = " and ".join(action_parts)
            if skipped:
                job_description += " *(currently skipped)*"
            job_description += "."
        else:
            job_description = f"Matillion job: {comp_name}."
            if skipped:
                job_description += " *(currently skipped)*"

        # Upstream always includes the parent Pipeline node
        job_upstream: list[dict[str, Any]] = [
            {
                "integration_name": "Matillion",
                "node_type": "Pipeline",
                "node_name": pipeline_name,
            }
        ]

        job_downstream: list[dict[str, Any]] = []

        if pipeline_type == "ingestion":
            # Ingestion: Job writes to a Snowflake landing table
            # Lineage: Matillion Job → Snowflake table ← dbt source
            if output_table:
                job_downstream.append(
                    {"table_name": _table_name_to_dbt_model(output_table)}
                )

        elif pipeline_type == "reverse_etl":
            # Reverse ETL: Job reads from a dbt model/view and pushes to destination
            # Lineage: dbt model → Matillion Job
            if source_table:
                job_upstream.append(
                    {"table_name": _table_name_to_dbt_model(source_table)}
                )

        job_node: dict[str, Any] = {
            "name": f"{pipeline_name}.{comp_name}",
            "node_type": "Job",
            "attributes": {
                "description": job_description,
                "url": file_url,
            },
            "lineage": {
                "upstream_dependencies": job_upstream,
                "downstream_dependencies": job_downstream,
            },
        }
        nodes.append(job_node)

    return nodes


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_and_save_nodes(
    repo_url: str,
    branch: str,
    github_token: str | None,
    pipeline_filter: list[str] | None = None,
) -> None:
    """
    Full extraction pipeline:
      1. Download the repository.
      2. Locate `.orch.yaml` files (all, or only those in ``pipeline_filter``).
      3. Parse each file.
      4. Convert to Paradime nodes.
      5. Write ``nodes.json``.

    Args:
        repo_url:         GitHub repository URL.
        branch:           Git branch to download.
        github_token:     Optional GitHub PAT for private repos.
        pipeline_filter:  Optional list of repo-relative file paths to process.
                          Pass ``None`` (default) to process every .orch.yaml.
    """
    script_dir = Path(__file__).resolve().parent
    temp_dir = script_dir / "temp_repo"

    # Resolve output path: <repo_root>/target/matillion_nodes.json
    repo_root = _find_repo_root(script_dir)
    target_dir = repo_root / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    output_file = target_dir / "matillion_nodes.json"

    logger.info(f"Output will be written to: {output_file}")

    # Step 1: Download the repository
    if not download_repo(repo_url, temp_dir, github_token, branch):
        logger.error("Failed to download repository. Aborting.")
        sys.exit(1)

    # Step 2: Find orchestration YAML files (all, or a filtered subset)
    if pipeline_filter:
        # User specified explicit file paths — resolve each against the temp dir
        orch_files = []
        for relative_path in pipeline_filter:
            candidate = temp_dir / relative_path
            if candidate.exists():
                orch_files.append(candidate)
            else:
                logger.warning(
                    f"Filtered pipeline file not found and will be skipped: "
                    f"'{relative_path}' (looked at {candidate})"
                )
        if not orch_files:
            logger.error(
                "None of the files in MATILLION_PIPELINE_FILTER were found. "
                "Check the paths are relative to the repo root."
            )
            sys.exit(1)
        logger.info(
            f"Filter active — processing {len(orch_files)} of "
            f"{len(list(temp_dir.glob('**/*.orch.yaml')))} available file(s): "
            + ", ".join(f.name for f in orch_files)
        )
    else:  # no filter — process everything
        # No filter — process every .orch.yaml in the repo
        orch_files = list(temp_dir.glob("**/*.orch.yaml"))
        if not orch_files:
            logger.error("No .orch.yaml files found in the repository.")
            sys.exit(1)
        logger.info(
            f"No filter set — processing all {len(orch_files)} orchestration file(s): "
            + ", ".join(f.name for f in orch_files)
        )

    # Step 3 & 4: Parse each file and accumulate nodes.
    # The try/finally guarantees temp_repo is removed even if parsing fails.
    all_nodes: list[dict[str, Any]] = []
    try:
        for orch_file in orch_files:
            # Relative path inside the repo (for building GitHub source links)
            relative_path = str(orch_file.relative_to(temp_dir))

            parsed = parse_matillion_yaml(orch_file)
            nodes = convert_to_paradime_nodes(
                parsed_pipeline=parsed,
                repo_url=repo_url,
                yaml_file_path=relative_path,
                branch=branch,
            )
            logger.info(
                f"  '{orch_file.name}' → {len(nodes)} node(s) "
                f"(1 pipeline + {len(nodes) - 1} job(s))"
            )
            all_nodes.extend(nodes)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            logger.info("Cleanup complete.")

    # Step 5: Write nodes.json
    output_file.write_text(json.dumps(all_nodes, indent=2), encoding="utf-8")
    logger.info(f"Saved {len(all_nodes)} total nodes to {output_file}")


if __name__ == "__main__":
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    if not GITHUB_TOKEN:
        logger.warning(
            "GITHUB_TOKEN not set – attempting anonymous download. "
            "This will fail for private repositories."
        )

    extract_and_save_nodes(
        repo_url=REPO_URL,
        branch=BRANCH,
        github_token=GITHUB_TOKEN,
        pipeline_filter=PIPELINE_FILTER,
    )
