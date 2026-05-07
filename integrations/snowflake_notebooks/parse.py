"""
Paradime Custom Integration for Snowflake Notebooks.

This script:
  1. Downloads a GitHub repository containing Snowflake Notebook files (.ipynb).
  2. Parses all ``*.ipynb`` files found (or those specified by SNOW_NOTEBOOK_FILE_FILTER).
  3. Converts each notebook and its SQL/Python cells into Paradime SDK nodes
     with lineage back to the dbt models whose output tables they query.
  4. Saves the resulting nodes to ``<repo_root>/target/snowflake_notebooks_nodes.json``.

Node model
----------
  Notebook – one per ``.ipynb`` file; represents the whole notebook.
  Cell     – one per named SQL or Python cell that references at least one table.

Lineage (Option B — dbt feeds the notebook)
-------------------------------------------
  dbt model → Snowflake table
    ← Cell (reads from the Snowflake table in its SQL source)
    ← Notebook (parent container, linked via downstream_dependencies)

Usage:
    python parse.py
    python parse.py /path/to/local/notebook.ipynb

Environment Variables:
    SNOW_NOTEBOOK_LOCAL_FILE    Absolute path to a local .ipynb file to parse (skips GitHub download)
    SNOW_NOTEBOOK_REPO_URL      GitHub repo URL
    SNOW_NOTEBOOK_BRANCH        Branch to download (default: dev-fdl-snow-workbook)
    SNOW_NOTEBOOK_FILE_FILTER   Comma-separated repo-relative .ipynb paths to parse.
                                Leave unset to process every .ipynb in the repo.
    GITHUB_TOKEN                GitHub Personal Access Token (required for private repos)
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
from src.parsers.snowflake_notebooks.parser import parse_notebook_file  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration – all overridable via environment variables
# ---------------------------------------------------------------------------
REPO_URL = os.getenv(
    "SNOW_NOTEBOOK_REPO_URL",
    "https://github.com/paradime-sandbox/paradime-dino-agent-snowflake",
)
BRANCH = os.getenv("SNOW_NOTEBOOK_BRANCH", "dev-fdl-snow-workbook")

# Optional comma-separated filter — leave unset to process every .ipynb
_raw_filter = os.getenv("SNOW_NOTEBOOK_FILE_FILTER", "").strip()
FILE_FILTER: list[str] | None = (
    [p.strip() for p in _raw_filter.split(",") if p.strip()]
    if _raw_filter
    else None
)

# Local file mode: skip GitHub download entirely.
# Set via env var or pass as the first CLI argument.
_local_file_arg = sys.argv[1] if len(sys.argv) > 1 else None
LOCAL_FILE: str | None = os.getenv("SNOW_NOTEBOOK_LOCAL_FILE") or _local_file_arg


# ---------------------------------------------------------------------------
# Repo root resolution  (copy-paste from _template — do NOT modify)
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
            logger.warning(
                "Could not locate dbt_project.yml — "
                "falling back to current working directory for output."
            )
            return Path.cwd()
        current = parent


# ---------------------------------------------------------------------------
# GitHub helpers  (copy-paste from _template — do NOT modify)
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

def convert_to_paradime_nodes(
    parsed_notebook: dict[str, Any],
    repo_url: str,
    notebook_file_path: str,
    branch: str,
) -> list[dict[str, Any]]:
    """
    Convert a parsed Snowflake Notebook into a list of Paradime SDK node dicts.

    One Notebook node is created per file.  One Cell node is created per code
    cell that carries a title and references at least one Snowflake table (via SQL).
    Python cells that do not have direct SQL table references are still included
    if they have a meaningful title, linked to the Notebook without table upstream
    dependencies.

    Lineage direction (Option B — dbt feeds the notebook):
        dbt model output → Snowflake table
            ← Cell.upstream_dependencies: [{"table_name": "<bare_table_name>"}]
            ← Cell.downstream_dependencies: [{"integration_name": "Snowflake Notebooks",
                                               "node_type": "Notebook",
                                               "node_name": "<notebook_name>"}]

    Args:
        parsed_notebook:    Output of :func:`parse_notebook_file`.
        repo_url:           GitHub repo URL (used to build source links).
        notebook_file_path: Relative path of the ``.ipynb`` inside the repo.
        branch:             Git branch name.

    Returns:
        List of node dictionaries ready to be serialised into ``nodes.json``.
    """
    nodes: list[dict[str, Any]] = []

    notebook_name: str = parsed_notebook["notebook_name"]
    description: str = parsed_notebook.get("description", "")
    cells: list[dict[str, Any]] = parsed_notebook["cells"]

    file_url = f"{repo_url}/blob/{branch}/{notebook_file_path}"

    # Count cells that carry actual table references (SQL cells)
    sql_cells = [c for c in cells if c["tables"]]

    # -----------------------------------------------------------------------
    # 1. Notebook node  (one per .ipynb file)
    # -----------------------------------------------------------------------
    notebook_description = description or (
        f"Snowflake Notebook with {len(cells)} code cell(s)"
        + (f", {len(sql_cells)} querying dbt model output" if sql_cells else "")
        + "."
    )

    notebook_node: dict[str, Any] = {
        "name": notebook_name,
        "node_type": "Notebook",
        "attributes": {
            "description": notebook_description,
            "url": file_url,
        },
        "lineage": {
            "upstream_dependencies": []
        },
    }
    nodes.append(notebook_node)

    # -----------------------------------------------------------------------
    # 2. Cell nodes  (one per meaningful code cell)
    # -----------------------------------------------------------------------
    seen_titles: dict[str, int] = {}  # de-duplicate title collisions

    for cell in cells:
        title: str = cell["title"]
        language: str = cell["language"]
        tables: list[str] = cell["tables"]
        snippet: str = cell["source_snippet"]

        # De-duplicate: append a counter when the same title appears multiple times
        if title in seen_titles:
            seen_titles[title] += 1
            unique_title = f"{title} ({seen_titles[title]})"
        else:
            seen_titles[title] = 1
            unique_title = title

        # Build a human-readable description
        if tables:
            tables_preview = ", ".join(f"`{t}`" for t in tables[:3])
            if len(tables) > 3:
                tables_preview += f" and {len(tables) - 3} more"
            cell_description = (
                f"{language.upper()} cell: **{unique_title}**. "
                f"Queries tables: {tables_preview}."
            )
        else:
            # Python cell or SQL cell with no detected table references
            cell_description = (
                f"{language.upper()} cell: **{unique_title}**."
                + (f" `{snippet[:120].strip()}…`" if snippet else "")
            )

        # Upstream: dbt models whose output tables this cell reads
        cell_upstream: list[dict[str, Any]] = [
            {"table_name": t} for t in tables
        ]

        # Downstream: the parent Notebook node
        cell_downstream: list[dict[str, Any]] = [
            {
                "integration_name": "Snowflake Notebooks",
                "node_type": "Notebook",
                "node_name": notebook_name,
            }
        ]

        cell_node: dict[str, Any] = {
            "name": f"{notebook_name}.{unique_title}",
            "node_type": "Cell",
            "attributes": {
                "description": cell_description,
                "url": file_url,
            },
            "lineage": {
                "upstream_dependencies": cell_upstream,
                "downstream_dependencies": cell_downstream,
            },
        }
        nodes.append(cell_node)

    return nodes


# ---------------------------------------------------------------------------
# Main extraction pipeline
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
      2. Locate ``.ipynb`` files (all, or only those in ``file_filter``).
      3. Parse each notebook.
      4. Convert to Paradime nodes.
      5. Write ``<repo_root>/target/snowflake_notebooks_nodes.json``.

    Args:
        repo_url:     GitHub repository URL.
        branch:       Git branch to download.
        github_token: Optional GitHub PAT for private repos.
        file_filter:  Optional list of repo-relative ``.ipynb`` file paths.
                      Pass ``None`` (default) to process every ``.ipynb``.
    """
    script_dir = Path(__file__).resolve().parent
    temp_dir = script_dir / "temp_repo"

    # Resolve output path: <repo_root>/target/snowflake_notebooks_nodes.json
    repo_root = _find_repo_root(script_dir)
    target_dir = repo_root / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    output_file = target_dir / "snowflake_notebooks_nodes.json"

    logger.info(f"Output will be written to: {output_file}")

    # Step 1: Download the repository
    if not download_repo(repo_url, temp_dir, github_token, branch):
        logger.error("Failed to download repository. Aborting.")
        sys.exit(1)

    # Step 2: Find notebook files (all, or a filtered subset)
    if file_filter:
        notebook_files: list[Path] = []
        for relative_path in file_filter:
            candidate = temp_dir / relative_path
            if candidate.exists():
                notebook_files.append(candidate)
            else:
                logger.warning(
                    f"Filtered notebook file not found and will be skipped: "
                    f"'{relative_path}' (looked at {candidate})"
                )
        if not notebook_files:
            logger.error(
                "None of the files in SNOW_NOTEBOOK_FILE_FILTER were found. "
                "Check the paths are relative to the repo root."
            )
            sys.exit(1)
        logger.info(
            f"Filter active — processing {len(notebook_files)} of "
            f"{len(list(temp_dir.glob('**/*.ipynb')))} available file(s): "
            + ", ".join(f.name for f in notebook_files)
        )
    else:
        # No filter — process every .ipynb in the repo
        notebook_files = list(temp_dir.glob("**/*.ipynb"))
        if not notebook_files:
            logger.error("No .ipynb files found in the repository.")
            sys.exit(1)
        logger.info(
            f"No filter set — processing all {len(notebook_files)} notebook(s): "
            + ", ".join(f.name for f in notebook_files)
        )

    # Steps 3 & 4: Parse each notebook and accumulate nodes.
    # The try/finally guarantees temp_repo is removed even if parsing fails.
    all_nodes: list[dict[str, Any]] = []
    try:
        for notebook_file in notebook_files:
            relative_path = str(notebook_file.relative_to(temp_dir))
            parsed = parse_notebook_file(notebook_file)
            nodes = convert_to_paradime_nodes(
                parsed_notebook=parsed,
                repo_url=repo_url,
                notebook_file_path=relative_path,
                branch=branch,
            )
            logger.info(
                f"  '{notebook_file.name}' → {len(nodes)} node(s) "
                f"(1 notebook + {len(nodes) - 1} cell(s))"
            )
            all_nodes.extend(nodes)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            logger.info("Cleanup complete.")

    # Step 5: Write nodes JSON
    output_file.write_text(json.dumps(all_nodes, indent=2), encoding="utf-8")
    logger.info(f"Saved {len(all_nodes)} total nodes to {output_file}")


if __name__ == "__main__":
    if LOCAL_FILE:
        local_path = Path(LOCAL_FILE)
        if not local_path.exists():
            logger.error(f"Local file not found: {local_path}")
            sys.exit(1)

        logger.info(f"Local file mode — parsing: {local_path}")

        script_dir = Path(__file__).resolve().parent
        repo_root = _find_repo_root(script_dir)
        target_dir = repo_root / "target"
        target_dir.mkdir(parents=True, exist_ok=True)
        output_file = target_dir / "snowflake_notebooks_nodes.json"

        parsed = parse_notebook_file(local_path)
        nodes = convert_to_paradime_nodes(
            parsed_notebook=parsed,
            repo_url=str(local_path.parent),
            notebook_file_path=local_path.name,
            branch="local",
        )
        logger.info(f"Generated {len(nodes)} node(s) (1 notebook + {len(nodes) - 1} cell(s))")

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
            file_filter=FILE_FILTER,
        )
