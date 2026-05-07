"""
Paradime Custom Integration for Streamlit Apps.

This script:
  1. Downloads a Streamlit repository from GitHub (as a ZIP archive).
  2. Parses one or more Streamlit app Python files found in the repo.
  3. Converts each app and its SQL-based charts into Paradime SDK nodes
     with lineage to the dbt models they query.
  4. Saves the resulting nodes to ``<repo_root>/target/streamlit_nodes.json``.

Node model
----------
  App   – one per Streamlit .py file, represents the whole application.
  Chart – one per SQL query / visualisation inside the app.

Lineage
-------
  dbt model
    ← Chart (queries the dbt model)
    ← App (contains the chart)

Usage:
    python parse.py
    python parse.py /path/to/local/streamlit_app.py

Environment Variables:
    STREAMLIT_LOCAL_FILE    Absolute path to a local .py file to parse (skips GitHub download)
    STREAMLIT_REPO_URL      GitHub repo URL (default: paradime-sandbox/streamlit-f1-analysis)
    STREAMLIT_BRANCH        Branch to download (default: main)
    STREAMLIT_APP_NAME      Display name for the Streamlit app node
    STREAMLIT_FILE_FILTER   Comma-separated repo-relative paths to parse.
                            Leave unset to use the built-in default file.
    GITHUB_TOKEN            GitHub Personal Access Token (required for private repos)
"""

from __future__ import annotations

# Standard library modules
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

# Third party modules
import duckdb

# ---------------------------------------------------------------------------
# Path setup – allow importing from src/
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.parsers.streamlit.parser import SQLTableTracker

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration – override via environment variables or edit defaults below
# ---------------------------------------------------------------------------
REPO_URL = os.getenv(
    "STREAMLIT_REPO_URL",
    "https://github.com/paradime-sandbox/streamlit-f1-analysis",
)
BRANCH = os.getenv("STREAMLIT_BRANCH", "main")
APP_NAME = os.getenv("STREAMLIT_APP_NAME", "F1 Analysis Dashboard")

# Optional comma-separated list of repo-relative .py file paths to parse.
# Leave unset (or empty) to use the built-in default path.
#
# Example (single file):
#   STREAMLIT_FILE_FILTER="streamlit_app.py"
#
# Example (multiple files):
#   STREAMLIT_FILE_FILTER="apps/dashboard.py,apps/explorer.py"
_raw_filter = os.getenv("STREAMLIT_FILE_FILTER", "").strip()
FILE_FILTER: list[str] | None = (
    [p.strip() for p in _raw_filter.split(",") if p.strip()]
    if _raw_filter
    else None
)

# Local file mode: skip GitHub download entirely.
# Set via env var or pass as the first CLI argument.
_local_file_arg = sys.argv[1] if len(sys.argv) > 1 else None
LOCAL_FILE: str | None = os.getenv("STREAMLIT_LOCAL_FILE") or _local_file_arg


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
# Streamlit app parsing
# ---------------------------------------------------------------------------

def parse_streamlit_app(app_path: Path, db_path: Path) -> None:
    """
    Parse a Streamlit app file and store results in DuckDB.

    Args:
        app_path: Path to the Streamlit app Python file.
        db_path:  Path to the DuckDB database file.
    """
    if db_path.exists():
        db_path.unlink()

    logger.info(f"Parsing Streamlit app: {app_path}")

    with SQLTableTracker(db_path=str(db_path)) as tracker:
        tracker.parse_file(str(app_path))
        results = tracker.get_results()
        logger.info(f"Found {len(results)} SQL queries in the app")


def read_parsed_data(db_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    Read parsed SQL data and app metadata from DuckDB.

    Args:
        db_path: Path to the DuckDB database file.

    Returns:
        Tuple of (list of chart dictionaries, app metadata dictionary or None).
    """
    conn = duckdb.connect(str(db_path))

    query = """
    SELECT
        id,
        file_path,
        chart_type,
        chart_name,
        chart_caption,
        line_number,
        sql_query,
        tables_used
    FROM table_usage
    ORDER BY id
    """
    result = conn.execute(query).fetchall()
    columns = [desc[0] for desc in conn.execute(query).description]
    charts = [dict(zip(columns, row)) for row in result]

    metadata_result = conn.execute(
        "SELECT app_title, app_description FROM app_metadata LIMIT 1"
    ).fetchone()
    app_metadata: dict[str, Any] | None = None
    if metadata_result:
        app_metadata = {
            "app_title": metadata_result[0],
            "app_description": metadata_result[1],
        }

    conn.close()
    return charts, app_metadata


# ---------------------------------------------------------------------------
# Node conversion helpers
# ---------------------------------------------------------------------------

def extract_dbt_model_from_table(table_name: str) -> str | None:
    """
    Extract a dbt model name from a fully qualified Snowflake table name.

    Examples:
        "ANALYTICS.DBT_PROD.INT_F1__RACE_RESULTS" → "int_f1__race_results"
        "int_f1__race_results"                     → "int_f1__race_results"

    Args:
        table_name: Fully qualified (or bare) table name.

    Returns:
        Lower-cased model name, or None for empty input.
    """
    if not table_name:
        return None

    parts = table_name.lower().split(".")
    return parts[-1] if len(parts) >= 1 else None


def convert_to_paradime_nodes(
    parsed_data: list[dict[str, Any]],
    app_metadata: dict[str, Any] | None,
    app_name: str,
    app_url: str,
) -> list[dict[str, Any]]:
    """
    Convert parsed Streamlit data to Paradime SDK node format.

    Creates one App node and one Chart node per SQL query that references
    at least one table.

    Args:
        parsed_data:  List of parsed SQL queries from DuckDB.
        app_metadata: Dictionary containing app_title and app_description (or None).
        app_name:     Display name for the App node.
        app_url:      URL to the Streamlit app source file.

    Returns:
        List of Paradime SDK node dictionaries.
    """
    nodes: list[dict[str, Any]] = []

    # Collect unique tables referenced across all queries
    all_tables: set[str] = set()
    for row in parsed_data:
        all_tables.update(row.get("tables_used") or [])

    # ------------------------------------------------------------------
    # App node (one per Streamlit file)
    # ------------------------------------------------------------------
    if app_metadata and app_metadata.get("app_title"):
        app_description = app_metadata["app_title"]
        if app_metadata.get("app_description"):
            app_description += f" - {app_metadata['app_description']}"
    else:
        tables_preview = ", ".join(list(all_tables)[:3])
        if len(all_tables) > 3:
            tables_preview += f" and {len(all_tables) - 3} more"
        app_description = (
            f"Streamlit application with {len(parsed_data)} SQL queries"
            + (f" using tables: {tables_preview}" if tables_preview else "")
        )

    nodes.append(
        {
            "name": app_name,
            "node_type": "App",
            "attributes": {
                "description": app_description,
                "url": app_url,
            },
            "lineage": {
                "upstream_dependencies": []
            },
        }
    )

    # ------------------------------------------------------------------
    # Chart nodes (one per SQL query that uses at least one table)
    # ------------------------------------------------------------------
    for row in parsed_data:
        tables: list[str] = row.get("tables_used") or []
        if not tables:
            continue

        chart_name = (
            row.get("chart_name")
            or f"Unnamed {row['chart_type']} (Line {row['line_number']})"
        )

        chart_caption = row.get("chart_caption")
        if chart_caption:
            chart_description = f"{chart_name} - {chart_caption}"
        else:
            tables_preview = ", ".join(tables[:2])
            if len(tables) > 2:
                tables_preview += f" and {len(tables) - 2} more"
            chart_description = (
                f"{row['chart_type']} chart (line {row['line_number']}) "
                f"using {tables_preview}"
            )

        # Upstream: dbt models that feed this chart
        chart_upstream = [
            {"table_name": extract_dbt_model_from_table(t)}
            for t in tables
            if extract_dbt_model_from_table(t)
        ]

        # Downstream: the App that contains this chart
        chart_downstream = [
            {
                "integration_name": "Streamlit",
                "node_type": "App",
                "node_name": app_name,
            }
        ]

        nodes.append(
            {
                "name": f"{app_name}.{chart_name}",
                "node_type": "Chart",
                "attributes": {
                    "description": chart_description,
                    "url": f"{app_url}#L{row['line_number']}",
                },
                "lineage": {
                    "upstream_dependencies": chart_upstream,
                    "downstream_dependencies": chart_downstream,
                },
            }
        )

    return nodes


# ---------------------------------------------------------------------------
# Main extraction pipeline
# ---------------------------------------------------------------------------

def extract_and_save_nodes(
    repo_url: str,
    branch: str,
    app_name: str,
    github_token: str | None,
    file_filter: list[str] | None = None,
) -> None:
    """
    Full extraction pipeline:
      1. Download the repository.
      2. Locate Streamlit .py files (from ``file_filter`` or the built-in default).
      3. Parse each file.
      4. Convert to Paradime nodes.
      5. Write ``<repo_root>/target/streamlit_nodes.json``.

    The output directory is always ``<repo_root>/target/`` — resolved by walking
    up from this script's location until ``dbt_project.yml`` is found.  The
    ``target/`` folder is created automatically if it does not yet exist.

    Args:
        repo_url:     GitHub repository URL.
        branch:       Git branch to download.
        app_name:     Display name used for the App node(s).
                      When multiple files are processed, each file's node is
                      named ``<app_name> – <filename>`` to keep names unique.
        github_token: Optional GitHub PAT for private repos.
        file_filter:  Optional list of repo-relative .py paths to process.
                      Pass ``None`` to fall back to the built-in default path.
    """
    script_dir = Path(__file__).resolve().parent
    temp_dir = script_dir / "temp_repo"
    db_path = script_dir / "streamlit_analysis.duckdb"

    # Resolve output path: <repo_root>/target/streamlit_nodes.json
    repo_root = _find_repo_root(script_dir)
    target_dir = repo_root / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    output_file = target_dir / "streamlit_nodes.json"

    logger.info(f"Output will be written to: {output_file}")

    # Step 1: Download the repository
    if not download_repo(repo_url, temp_dir, github_token, branch):
        logger.error("Failed to download repository. Aborting.")
        sys.exit(1)

    # Step 2: Resolve which files to process
    if file_filter:
        app_files: list[Path] = []
        for relative_path in file_filter:
            candidate = temp_dir / relative_path
            if candidate.exists():
                app_files.append(candidate)
            else:
                logger.warning(
                    f"Filtered file not found and will be skipped: "
                    f"'{relative_path}' (looked at {candidate})"
                )
        if not app_files:
            logger.error(
                "None of the files in STREAMLIT_FILE_FILTER were found in the repo. "
                "Check that the paths are relative to the repo root."
            )
            sys.exit(1)
        logger.info(
            f"Filter active — processing {len(app_files)} file(s): "
            + ", ".join(f.name for f in app_files)
        )
    else:
        # Built-in default: the single known app file in the sandbox repo
        default_path = "H9RQV0GD3DTOB7H0/streamlit_app.py"
        default_file = temp_dir / default_path
        if not default_file.exists():
            logger.error(
                f"Default Streamlit file not found: '{default_path}'. "
                "Set STREAMLIT_FILE_FILTER to the correct repo-relative path."
            )
            sys.exit(1)
        app_files = [default_file]
        logger.info(f"No filter set — using default file: '{default_path}'")

    # Step 3 & 4: Parse each file and accumulate nodes.
    # try/finally guarantees temp_repo is removed even if parsing fails.
    all_nodes: list[dict[str, Any]] = []
    failed_files: list[str] = []
    try:
        for app_file in app_files:
            relative_path = str(app_file.relative_to(temp_dir))

            # Use disambiguated app name when processing multiple files
            file_app_name = (
                app_name
                if len(app_files) == 1
                else f"{app_name} – {app_file.stem}"
            )

            try:
                parse_streamlit_app(app_file, db_path)
                parsed_data, app_metadata = read_parsed_data(db_path)

                logger.info(
                    f"  '{relative_path}' → {len(parsed_data)} SQL queries found"
                )
                if app_metadata:
                    logger.info(f"    App Title: {app_metadata.get('app_title')}")

                app_url = f"{repo_url}/blob/{branch}/{relative_path}"
                nodes = convert_to_paradime_nodes(
                    parsed_data=parsed_data,
                    app_metadata=app_metadata,
                    app_name=file_app_name,
                    app_url=app_url,
                )
                logger.info(
                    f"  '{app_file.name}' → {len(nodes)} node(s) "
                    f"(1 app + {len(nodes) - 1} chart(s))"
                )
                all_nodes.extend(nodes)
            except Exception as exc:
                logger.error(
                    f"  Failed to parse '{app_file.name}' — skipping. Error: {exc}"
                )
                failed_files.append(relative_path)

            # Remove the per-file DuckDB database before processing the next file
            if db_path.exists():
                db_path.unlink()

    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            logger.info("Cleanup complete.")

    # Step 5: Write streamlit_nodes.json into target/
    output_file.write_text(json.dumps(all_nodes, indent=2), encoding="utf-8")
    logger.info(f"Saved {len(all_nodes)} total nodes to {output_file}")

    # Step 6: Write failed files log (only if there were failures)
    if failed_files:
        failed_log = target_dir / "streamlit_parse_failures.txt"
        failed_log.write_text("\n".join(failed_files) + "\n", encoding="utf-8")
        logger.warning(
            f"{len(failed_files)} file(s) could not be parsed and were skipped:"
        )
        for path in failed_files:
            logger.warning(f"  - {path}")
        logger.warning(f"Full list written to {failed_log}")


if __name__ == "__main__":
    if LOCAL_FILE:
        local_path = Path(LOCAL_FILE)
        if not local_path.exists():
            logger.error(f"Local file not found: {local_path}")
            sys.exit(1)

        logger.info(f"Local file mode — parsing: {local_path}")

        script_dir = Path(__file__).resolve().parent
        db_path = script_dir / "streamlit_analysis.duckdb"
        repo_root = _find_repo_root(script_dir)
        target_dir = repo_root / "target"
        target_dir.mkdir(parents=True, exist_ok=True)
        output_file = target_dir / "streamlit_nodes.json"

        parse_streamlit_app(local_path, db_path)
        parsed_data, app_metadata = read_parsed_data(db_path)
        if db_path.exists():
            db_path.unlink()

        logger.info(f"Found {len(parsed_data)} SQL queries")
        nodes = convert_to_paradime_nodes(
            parsed_data=parsed_data,
            app_metadata=app_metadata,
            app_name=APP_NAME,
            app_url=str(local_path),
        )
        logger.info(f"Generated {len(nodes)} node(s) (1 app + {len(nodes) - 1} chart(s))")

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
            app_name=APP_NAME,
            github_token=GITHUB_TOKEN,
            file_filter=FILE_FILTER,
        )
