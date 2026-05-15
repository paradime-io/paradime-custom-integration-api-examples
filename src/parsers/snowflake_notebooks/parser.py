"""
Snowflake Notebooks Parser.

Parses Snowflake Notebook ``.ipynb`` files and extracts:
  - Notebook-level metadata (name, description from first markdown cell)
  - Cell-level details (title, language, SQL table references)

The parser produces structured data ready to be converted into
Paradime custom integration nodes.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp, parse_one

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex / constants
# ---------------------------------------------------------------------------

# Snowflake Notebooks embed SQL as %%sql magic blocks.
# We strip the magic header before parsing.
_MAGIC_RE = re.compile(r"^%%sql[^\n]*\n?", re.IGNORECASE)

# Match Python f-string placeholders like {var} or {obj.attr}.
_PLACEHOLDER_RE = re.compile(r"\{[^}]+\}")

# Substitution token used in place of f-string placeholders. Chosen so that
# `placeholder.placeholder.table` parses as a 3-part identifier rather than
# a numeric literal (which would happen with '1' → `1.1.table`).
_PLACEHOLDER_TOKEN = "placeholder"


# ---------------------------------------------------------------------------
# SQL table extraction
# ---------------------------------------------------------------------------

def extract_tables_from_sql(sql_source: str, dialect: str = "snowflake") -> list[str]:
    """
    Return unique lower-cased bare table names that the SQL READS from.

    Uses sqlglot to walk the parse tree, restricting collection to tables that
    appear under FROM / JOIN clauses. INSERT-target tables and CTE self-refs
    are excluded so they don't pollute upstream lineage.

    f-string placeholders (``{db}``, ``{sch}``, …) are substituted with a safe
    identifier before parsing so partially-templated SQL still parses cleanly.

    Args:
        sql_source: Raw SQL string. ``%%sql`` magic headers and f-string
            placeholders are handled transparently.
        dialect:    sqlglot dialect (default: ``"snowflake"``).

    Returns:
        Sorted list of unique lower-cased bare table names.
    """
    clean_sql = _MAGIC_RE.sub("", sql_source).strip()
    if not clean_sql:
        return []

    processed = _PLACEHOLDER_RE.sub(_PLACEHOLDER_TOKEN, clean_sql)

    try:
        parsed = parse_one(processed, dialect=dialect)
    except sqlglot.errors.ParseError as exc:
        logger.debug(f"sqlglot parse error: {exc}")
        return []
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug(f"sqlglot unexpected error: {exc}")
        return []

    if parsed is None:
        return []

    # Names declared as CTEs in this query — references to them should be
    # treated as local aliases, not as upstream tables (unless they happen to
    # appear with a db/catalog qualifier, which would mean they shadow a real
    # table — rare, but possible).
    cte_names = {cte.alias_or_name.lower() for cte in parsed.find_all(exp.CTE)}

    found: set[str] = set()
    for clause in parsed.find_all(exp.From, exp.Join):
        for table in clause.find_all(exp.Table):
            name = (table.name or "").lower()
            if not name or name == _PLACEHOLDER_TOKEN:
                continue
            if name in cte_names and not (table.db or table.catalog):
                continue
            found.add(name)

    return sorted(found)


# ---------------------------------------------------------------------------
# Python-cell SQL extraction
# ---------------------------------------------------------------------------

def _looks_like_sql(text: str) -> bool:
    """Cheap heuristic: does this string look like a SELECT/CTE query?"""
    if len(text.strip()) < 15:
        return False
    upper = text.upper().strip()
    is_select = upper.startswith("SELECT")
    is_cte = upper.startswith("WITH") and "SELECT" in upper
    if not (is_select or is_cte):
        return False
    return "FROM" in upper


def _extract_sql_strings_from_python(source: str) -> list[str]:
    """
    Walk a Python source cell and return SQL-looking string literals.

    Handles both plain string constants and f-strings (``ast.JoinedStr``).
    For f-strings the literal chunks are re-stitched with a ``{x}`` placeholder
    in place of each ``FormattedValue`` so the resulting string is parseable —
    otherwise ``ast.walk`` would visit each literal chunk in isolation and
    split a SQL statement mid-clause.

    Args:
        source: Python source code for a single notebook cell.

    Returns:
        List of SQL-looking strings (de-duplicated, original order preserved).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    seen: set[str] = set()
    out: list[str] = []

    def _consider(text: str) -> None:
        key = text.strip()
        if not key or key in seen:
            return
        if not _looks_like_sql(key):
            return
        seen.add(key)
        out.append(key)

    def _visit(node: ast.AST) -> None:
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                elif isinstance(value, ast.FormattedValue):
                    parts.append("{x}")
            _consider("".join(parts))
            return  # do not descend into the literal chunks
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            _consider(node.value)
        for child in ast.iter_child_nodes(node):
            _visit(child)

    _visit(tree)
    return out


# ---------------------------------------------------------------------------
# Notebook parsing
# ---------------------------------------------------------------------------

def parse_notebook_file(file_path: Path) -> dict[str, Any]:
    """
    Parse a Snowflake Notebook ``.ipynb`` file and extract cell metadata.

    Each code cell is examined for:
      - A ``title`` metadata attribute (Snowflake Notebooks extension)
      - Its language (``sql`` or ``python``)
      - SQL table references — for SQL cells, extracted directly from the
        cell source; for Python cells, extracted from any embedded SQL
        string literals or f-strings (e.g. ``session.sql(f"…")``).

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
              - ``tables``        (list[str])  bare table names
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
        if cell_type != "code":
            continue

        metadata: dict[str, Any] = cell.get("metadata", {})
        language: str = metadata.get("language", "python").lower()
        title: str = metadata.get("title", "").strip()
        source: str = "".join(cell.get("source", []))

        tables: list[str] = []
        if language == "sql":
            tables = extract_tables_from_sql(source)
        elif language == "python":
            collected: set[str] = set()
            for sql_text in _extract_sql_strings_from_python(source):
                collected.update(extract_tables_from_sql(sql_text))
            tables = sorted(collected)

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
