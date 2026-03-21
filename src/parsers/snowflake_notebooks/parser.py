"""
Snowflake Notebooks Parser.

Parses Snowflake Notebook ``.ipynb`` files and extracts:
  - Notebook-level metadata (name, description from first markdown cell)
  - Cell-level details (title, language, SQL table references)

The parser produces structured data ready to be converted into
Paradime custom integration nodes.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches fully-qualified Snowflake table references: DATABASE.SCHEMA.TABLE
# as well as two-part SCHEMA.TABLE references that appear after FROM / JOIN.
_SQL_TABLE_RE = re.compile(
    r"""
    (?:FROM|JOIN)\s+          # preceded by FROM or JOIN keyword
    (                         # capture group — the table reference
        [\w$][\w$]*           # optional database or schema segment
        (?:\.[\w$][\w$]*){1,2}  # one or two dot-separated name parts
    )
    (?:\s|;|$|\))             # followed by whitespace, semicolon, end, or paren
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Snowflake Notebooks embed SQL as %%sql magic blocks.
# We strip the magic header before parsing.
_MAGIC_RE = re.compile(r"^%%sql[^\n]*\n?", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Core parsing functions
# ---------------------------------------------------------------------------

def extract_tables_from_sql(sql_source: str) -> list[str]:
    """
    Extract unique lower-cased table names referenced in a SQL snippet.

    Handles both fully-qualified (DB.SCHEMA.TABLE) and two-part (SCHEMA.TABLE)
    references that follow a FROM or JOIN keyword.  The Snowflake Notebooks
    ``%%sql`` magic header is stripped before scanning.

    Args:
        sql_source: Raw SQL string (may include a ``%%sql`` magic header).

    Returns:
        Sorted list of unique lower-cased table names (bare name only, no
        schema or database prefix).
    """
    # Remove Snowflake Notebooks magic prefix if present
    clean_sql = _MAGIC_RE.sub("", sql_source).strip()

    found: set[str] = set()
    for match in _SQL_TABLE_RE.finditer(clean_sql):
        full_ref = match.group(1).strip()
        # Take only the last segment (bare table name) so it matches dbt model names
        bare_name = full_ref.split(".")[-1].lower()
        # Skip obvious SQL keywords that can appear after FROM (e.g. sub-queries)
        if bare_name not in {"select", "where", "join", "on", "as", "with"}:
            found.add(bare_name)

    return sorted(found)


def parse_notebook_file(file_path: Path) -> dict[str, Any]:
    """
    Parse a Snowflake Notebook ``.ipynb`` file and extract cell metadata.

    Each code cell is examined for:
    - A ``title`` metadata attribute (Snowflake Notebooks extension)
    - Its language (``sql`` or ``python``)
    - SQL table references extracted via :func:`extract_tables_from_sql`

    The notebook description is taken from the first non-empty line of the
    first markdown cell (stripping any Markdown heading prefixes).

    Args:
        file_path: Path to the ``.ipynb`` file.

    Returns:
        Dictionary with keys:
          - ``notebook_name`` (str): human-friendly name from the filename
          - ``description``   (str): notebook-level description
          - ``cells``         (list[dict]): list of parsed cell records, each with:
              - ``cell_index``    (int)
              - ``title``         (str)
              - ``language``      (str)  ``"sql"`` or ``"python"``
              - ``tables``        (list[str])  bare table names (SQL cells only)
              - ``source_snippet``(str)  first 200 chars of source for descriptions
    """
    logger.info(f"Parsing notebook: {file_path.name}")

    try:
        raw: dict[str, Any] = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"Failed to read {file_path}: {exc}")
        return {"notebook_name": file_path.stem, "description": "", "cells": []}

    nb_cells: list[dict[str, Any]] = raw.get("cells", [])
    notebook_name: str = file_path.stem.replace("_", " ").title()

    # Pull description from the first markdown cell (if any)
    description = ""
    for cell in nb_cells:
        if cell.get("cell_type") == "markdown":
            raw_md = "".join(cell.get("source", []))
            for line in raw_md.splitlines():
                stripped = line.lstrip("#").strip()
                if stripped:
                    description = stripped
                    break
            if description:
                break

    parsed_cells: list[dict[str, Any]] = []

    for idx, cell in enumerate(nb_cells):
        cell_type: str = cell.get("cell_type", "")

        # Only process code cells
        if cell_type != "code":
            continue

        metadata: dict[str, Any] = cell.get("metadata", {})
        language: str = metadata.get("language", "python").lower()
        title: str = metadata.get("title", "").strip()
        source: str = "".join(cell.get("source", []))

        tables: list[str] = []
        if language == "sql":
            tables = extract_tables_from_sql(source)

        if not title:
            title = f"Cell {idx + 1} ({language})"

        parsed_cells.append(
            {
                "cell_index": idx,
                "title": title,
                "language": language,
                "tables": tables,
                "source_snippet": source[:200],
            }
        )

    logger.info(
        f"  → {len(parsed_cells)} code cell(s) found "
        f"({sum(1 for c in parsed_cells if c['tables'])} with SQL table references)"
    )

    return {
        "notebook_name": notebook_name,
        "description": description,
        "cells": parsed_cells,
    }
