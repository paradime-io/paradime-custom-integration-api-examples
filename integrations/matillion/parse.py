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
    python parse.py /path/to/local/pipeline.orch.yaml

Environment Variables:
    MATILLION_LOCAL_FILE    Absolute path to a local .orch.yaml file to parse (skips GitHub download)
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

# Local file mode: skip GitHub download entirely.
# Set via env var or pass as the first CLI argument.
_local_file_arg = sys.argv[1] if len(sys.argv) > 1 else None
LOCAL_FILE: str | None = os.getenv("MATILLION_LOCAL_FILE") or _local_file_arg

# Local directory mode: process all .orch.yaml files in a directory.
LOCAL_DIR: str | None = os.getenv("MATILLION_LOCAL_DIR")


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


# Roles that always represent real data movement or orchestration —
# emit a Job node unconditionally.
_ALWAYS_EMIT_ROLES = {
    "ingestion_input",
    "reverse_etl_output",
    "orchestrator_call",
    "shared_pipeline_call",
}


def _component_is_data_bearing(component: dict[str, Any]) -> bool:
    """
    Decide whether a component should become a Paradime Job node.

    The data axis only cares about components that:
      - move data into Snowflake (ingestion connector)
      - move data out of Snowflake (reverse-ETL connector)
      - call another pipeline (orchestrator / shared-pipeline)
      - run SQL that has BOTH a source and a target table

    Everything else (control flow, variable juggling, debug prints, audit
    inserts with no FROM clause, TRUNCATEs, webhook alerts) is skipped so
    the Paradime UI shows only meaningful lineage nodes.
    """
    role = component.get("role")
    if role in _ALWAYS_EMIT_ROLES:
        return True
    if role == "sql_transform":
        # Only emit SQL jobs whose script produces a real source → target edge.
        # Audit-table inserts, TRUNCATEs, and pure UPDATEs have a target but
        # no source and contribute no lineage.
        return bool(
            component.get("sql_sources") and component.get("sql_targets")
        )
    return False


def build_pipeline_index(
    parsed_results: list[tuple[Path, dict[str, Any]]],
) -> dict[str, str]:
    """
    Build a lookup so ``run-orchestration`` calls can resolve to a Pipeline node.

    Maps the source-file *stem* (without ``.orch``) → the pipeline display name
    emitted by :func:`parse_matillion_yaml`. Matillion's ``orchestrationJob``
    parameter ends in the same identifier, so we can match on it directly.

    Args:
        parsed_results: List of ``(path, parsed_pipeline_dict)`` pairs.

    Returns:
        Dict from stem → pipeline_name.
    """
    index: dict[str, str] = {}
    for path, parsed in parsed_results:
        stem = path.stem
        if stem.endswith(".orch"):
            stem = stem[: -len(".orch")]
        index[stem] = parsed["pipeline_name"]
    return index


def build_table_io_indexes(
    parsed_results: list[tuple[Path, dict[str, Any]]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """
    Index every Snowflake table touched by the parsed pipelines.

    For each table, record which Job(s) produce it (write) and which Job(s)
    consume it (read). The converter uses these indexes to short-circuit
    Matillion-internal handoffs: when a table is produced and consumed
    *within the parsed set*, the table node is dropped from lineage and a
    direct Job → Job edge is emitted instead.

    Sources of producer/consumer signal:
      * ``sql-executor`` ``sql_targets`` / ``sql_sources``
      * ``reverse_etl_output`` ``source_table`` (consumer)
      * ``ingestion_input`` ``output_table`` (producer)

    Args:
        parsed_results: List of ``(path, parsed_pipeline_dict)`` pairs.

    Returns:
        ``(producers, consumers)`` — each is a dict mapping lowercased
        ``table_name`` → list of fully-qualified Job names
        (``"<pipeline_name>.<component_name>"``).
    """
    producers: dict[str, list[str]] = {}
    consumers: dict[str, list[str]] = {}

    for _path, parsed in parsed_results:
        pipeline_name = parsed["pipeline_name"]
        pipeline_type = parsed.get("pipeline_type", "unknown")
        for comp in parsed.get("components", []):
            job_name = f"{pipeline_name}.{comp['name']}"
            for tgt in comp.get("sql_targets") or []:
                producers.setdefault(tgt.lower(), []).append(job_name)
            for src in comp.get("sql_sources") or []:
                consumers.setdefault(src.lower(), []).append(job_name)
            if pipeline_type == "reverse_etl" and comp.get("source_table"):
                consumers.setdefault(comp["source_table"].lower(), []).append(job_name)
            if pipeline_type == "ingestion" and comp.get("output_table"):
                producers.setdefault(comp["output_table"].lower(), []).append(job_name)

    return producers, consumers


def convert_to_paradime_nodes(
    parsed_pipeline: dict[str, Any],
    repo_url: str,
    yaml_file_path: str,
    branch: str = "main",
    pipeline_index: dict[str, str] | None = None,
    table_producers: dict[str, list[str]] | None = None,
    table_consumers: dict[str, list[str]] | None = None,
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
        "orchestrator": "orchestrator (calls other pipelines)",
        "sql_transform": "SQL transform",
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
    # 2. Job nodes  (one per data-bearing or orchestration component)
    # ------------------------------------------------------------------
    for component in components:
        # Only emit Jobs for components that contribute to data lineage or
        # orchestration. Everything else (control flow, audit SQL, debug
        # prints, query-to-scalar variable updates, webhooks) is dropped.
        if not _component_is_data_bearing(component):
            continue

        comp_type: str = component["type"]
        comp_name: str = component["name"]
        role: str = component["role"]
        output_table: str | None = component["output_table"]
        source_table: str | None = component["source_table"]
        endpoint: str | None = component["endpoint"]
        skipped: bool = component["skipped"]
        sql_sources: list[str] = component.get("sql_sources", []) or []
        sql_targets: list[str] = component.get("sql_targets", []) or []
        called_pipeline: str | None = component.get("called_pipeline")
        shared_pipeline_name: str | None = component.get("shared_pipeline_name")
        shared_pipeline_vars: dict[str, str] = component.get("shared_pipeline_vars") or {}

        # Build a readable description from whatever signal the component carries
        action_parts: list[str] = []
        if endpoint:
            action_parts.append(f"Calls the **{endpoint}** API endpoint")
        if pipeline_type == "ingestion" and output_table:
            action_parts.append(f"writes results to Snowflake table `{output_table}`")
        elif pipeline_type == "reverse_etl" and source_table:
            action_parts.append(f"reads from Snowflake table/view `{source_table}`")
        if role == "sql_transform" and (sql_sources or sql_targets):
            if sql_targets and sql_sources:
                action_parts.append(
                    f"runs SQL writing `{', '.join(sql_targets)}` from `{', '.join(sql_sources)}`"
                )
            elif sql_targets:
                action_parts.append(f"runs SQL writing `{', '.join(sql_targets)}`")
        if role == "orchestrator_call" and called_pipeline:
            action_parts.append(f"runs orchestration `{called_pipeline}`")
        if role == "shared_pipeline_call" and shared_pipeline_name:
            dbt_id = shared_pipeline_vars.get("DBT_JOB_ID")
            if dbt_id:
                action_parts.append(
                    f"runs shared pipeline `{shared_pipeline_name}` (dbt job `{dbt_id}`)"
                )
            else:
                action_parts.append(f"runs shared pipeline `{shared_pipeline_name}`")

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

        producers_idx = table_producers or {}
        consumers_idx = table_consumers or {}
        own_job_full_name = f"{pipeline_name}.{comp_name}"

        def _add_upstream_table(table: str) -> None:
            """Add a table as an upstream dependency, unless it's produced by
            another Matillion Job we parsed — in which case the producer's
            downstream edge will create the connection directly and we skip
            the table node entirely (no view-as-staging-node clutter)."""
            key = table.lower()
            if key in producers_idx:
                # Internal Matillion handoff — producer Job will link to us
                return
            job_upstream.append({"table_name": _table_name_to_dbt_model(table)})

        def _add_downstream_table(table: str) -> None:
            """Add a table as a downstream dependency. If another Matillion
            Job in the parsed set reads this table, replace the table edge
            with direct Job → Job edges (the table is internal plumbing).
            Otherwise keep the table_name so external assets (dbt, …) link."""
            key = table.lower()
            internal_consumers = consumers_idx.get(key, [])
            internal_consumers = [c for c in internal_consumers if c != own_job_full_name]
            if internal_consumers:
                for consumer_full_name in internal_consumers:
                    job_downstream.append({
                        "integration_name": "Matillion",
                        "node_type": "Job",
                        "node_name": consumer_full_name,
                    })
                return
            job_downstream.append({"table_name": _table_name_to_dbt_model(table)})

        # ---- Data lineage from connector-style components -----------------
        if pipeline_type == "ingestion" and output_table:
            _add_downstream_table(output_table)
        elif pipeline_type == "reverse_etl" and source_table:
            _add_upstream_table(source_table)

        # ---- Data lineage from sql-executor scripts -----------------------
        # Works regardless of the parent pipeline_type. This is what connects
        # dbt models → Matillion-built views → reverse-ETL pipelines.
        for src in sql_sources:
            _add_upstream_table(src)
        for tgt in sql_targets:
            _add_downstream_table(tgt)

        # ---- Orchestration lineage: run-orchestration -> child Pipeline ---
        if role == "orchestrator_call" and called_pipeline and pipeline_index:
            resolved = pipeline_index.get(called_pipeline)
            if resolved:
                job_downstream.append({
                    "integration_name": "Matillion",
                    "node_type": "Pipeline",
                    "node_name": resolved,
                })
            else:
                logger.warning(
                    f"  '{pipeline_name}' calls orchestration '{called_pipeline}' "
                    "but no matching .orch.yaml was parsed — cross-pipeline edge skipped."
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
    failed_files: list[str] = []
    try:
        # ---- Pass 1: parse every YAML so we can build the cross-pipeline
        # index before emitting any nodes (otherwise run-orchestration calls
        # can't resolve to a Pipeline node).
        parsed_results: list[tuple[Path, dict[str, Any]]] = []
        for orch_file in orch_files:
            relative_path = str(orch_file.relative_to(temp_dir))
            try:
                parsed = parse_matillion_yaml(orch_file)
            except Exception as exc:
                logger.error(
                    f"  Failed to parse '{orch_file.name}' — skipping. Error: {exc}"
                )
                failed_files.append(relative_path)
                continue
            parsed_results.append((orch_file, parsed))

        pipeline_index = build_pipeline_index(parsed_results)
        table_producers, table_consumers = build_table_io_indexes(parsed_results)

        # ---- Pass 2: convert each parsed pipeline to Paradime nodes,
        # using the indexes to resolve cross-pipeline orchestration edges
        # and to collapse Matillion-internal table handoffs.
        for orch_file, parsed in parsed_results:
            relative_path = str(orch_file.relative_to(temp_dir))
            try:
                nodes = convert_to_paradime_nodes(
                    parsed_pipeline=parsed,
                    repo_url=repo_url,
                    yaml_file_path=relative_path,
                    branch=branch,
                    pipeline_index=pipeline_index,
                    table_producers=table_producers,
                    table_consumers=table_consumers,
                )
            except Exception as exc:
                logger.error(
                    f"  Failed to convert '{orch_file.name}' — skipping. Error: {exc}"
                )
                failed_files.append(relative_path)
                continue

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

    # Step 6: Write failed files log (only if there were failures)
    if failed_files:
        failed_log = target_dir / "matillion_parse_failures.txt"
        failed_log.write_text("\n".join(failed_files) + "\n", encoding="utf-8")
        logger.warning(
            f"{len(failed_files)} file(s) could not be parsed and were skipped:"
        )
        for path in failed_files:
            logger.warning(f"  - {path}")
        logger.warning(f"Full list written to {failed_log}")


if __name__ == "__main__":
    if LOCAL_DIR:
        local_dir_path = Path(LOCAL_DIR)
        if not local_dir_path.is_dir():
            logger.error(f"Local directory not found: {local_dir_path}")
            sys.exit(1)

        orch_files = list(local_dir_path.glob("**/*.orch.yaml"))
        if not orch_files:
            logger.error(f"No .orch.yaml files found in: {local_dir_path}")
            sys.exit(1)

        logger.info(f"Local directory mode — processing {len(orch_files)} file(s) from {local_dir_path}")

        script_dir = Path(__file__).resolve().parent
        repo_root = _find_repo_root(script_dir)
        target_dir = repo_root / "target"
        target_dir.mkdir(parents=True, exist_ok=True)
        output_file = target_dir / "matillion_nodes.json"

        # Pass 1: parse all files so we can build the cross-pipeline index
        parsed_results: list[tuple[Path, dict]] = []
        for orch_file in orch_files:
            try:
                parsed = parse_matillion_yaml(orch_file)
                parsed_results.append((orch_file, parsed))
            except Exception as exc:
                logger.error(f"  Failed to parse '{orch_file.name}' — skipping. Error: {exc}")

        pipeline_index = build_pipeline_index(parsed_results)
        table_producers, table_consumers = build_table_io_indexes(parsed_results)

        # Pass 2: convert with index lookup
        all_nodes: list[dict] = []
        for orch_file, parsed in parsed_results:
            try:
                nodes = convert_to_paradime_nodes(
                    parsed_pipeline=parsed,
                    repo_url=str(local_dir_path),
                    yaml_file_path=orch_file.name,
                    branch="local",
                    pipeline_index=pipeline_index,
                    table_producers=table_producers,
                    table_consumers=table_consumers,
                )
                logger.info(f"  '{orch_file.name}' → {len(nodes)} node(s) (1 pipeline + {len(nodes) - 1} job(s))")
                all_nodes.extend(nodes)
            except Exception as exc:
                logger.error(f"  Failed to convert '{orch_file.name}' — skipping. Error: {exc}")

        output_file.write_text(json.dumps(all_nodes, indent=2), encoding="utf-8")
        logger.info(f"Saved {len(all_nodes)} total nodes to {output_file}")

    elif LOCAL_FILE:
        local_path = Path(LOCAL_FILE)
        if not local_path.exists():
            logger.error(f"Local file not found: {local_path}")
            sys.exit(1)

        logger.info(f"Local file mode — parsing: {local_path}")

        script_dir = Path(__file__).resolve().parent
        repo_root = _find_repo_root(script_dir)
        target_dir = repo_root / "target"
        target_dir.mkdir(parents=True, exist_ok=True)
        output_file = target_dir / "matillion_nodes.json"

        parsed = parse_matillion_yaml(local_path)
        # Single-file mode: only this file in the index, so cross-pipeline
        # edges to other files will resolve to "unknown" and be logged.
        pipeline_index = build_pipeline_index([(local_path, parsed)])
        table_producers, table_consumers = build_table_io_indexes([(local_path, parsed)])
        nodes = convert_to_paradime_nodes(
            parsed_pipeline=parsed,
            repo_url=str(local_path.parent),
            yaml_file_path=local_path.name,
            branch="local",
            pipeline_index=pipeline_index,
            table_producers=table_producers,
            table_consumers=table_consumers,
        )
        logger.info(f"Generated {len(nodes)} node(s) (1 pipeline + {len(nodes) - 1} job(s))")

        output_file.write_text(json.dumps(nodes, indent=2), encoding="utf-8")
        logger.info(f"Saved to {output_file}")
    else:
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
