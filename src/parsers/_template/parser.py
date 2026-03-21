"""
[YOUR TOOL NAME] Parser.

Replace every occurrence of [YOUR TOOL NAME] / [your_tool] with your actual
tool name before committing.

This module is the PARSER layer — it contains only pure parsing functions.

Rules for this file:
  ✓  Read and interpret source files (YAML, JSON, SQL, Python AST, etc.)
  ✓  Return plain Python dicts / lists — no SDK types
  ✓  Use logging for diagnostics
  ✗  No network calls (no urlopen, requests, etc.)
  ✗  No sys.exit() — raise exceptions instead
  ✗  No file I/O beyond reading the input file passed as an argument
  ✗  No Paradime SDK imports

The orchestration layer (``integrations/[your_tool]/parse.py``) handles
everything else: downloading the repo, calling these functions, converting
the output to Paradime node dicts, and writing the JSON file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TODO: add any helper functions your parser needs (e.g. regex extractors)
# ---------------------------------------------------------------------------


def parse_source_file(file_path: Path) -> dict[str, Any]:
    """
    Parse a single source file and return structured data.

    Replace this stub with your actual parsing logic.

    The return value is a plain dict — it will be passed to
    ``convert_to_paradime_nodes()`` in ``integrations/[your_tool]/parse.py``.

    Suggested return shape (adapt as needed)::

        {
            "name":        str,           # human-friendly name for the parent node
            "description": str,           # optional description
            "records": [                  # one entry per child node
                {
                    "name":  str,         # child display name
                    "table": str,         # Snowflake table referenced (if any)
                    ...                   # any extra fields your converter needs
                },
                ...
            ]
        }

    Args:
        file_path: Path to the source file to parse.

    Returns:
        Structured dict ready to be passed to ``convert_to_paradime_nodes()``.
    """
    logger.info(f"Parsing: {file_path.name}")

    # TODO: replace this stub with real parsing logic
    # Example:
    #   raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    #   ...

    name = file_path.stem.replace("_", " ").title()

    return {
        "name": name,
        "description": "",
        "records": [],
    }
