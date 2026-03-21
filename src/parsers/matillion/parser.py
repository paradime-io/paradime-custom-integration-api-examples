"""
Matillion Orchestration Pipeline Parser.

Parses Matillion `.orch.yaml` files and extracts:
  - Pipeline-level metadata (name, description)
  - Component-level details (type, endpoint, output table, skipped flag)
  - Transition graph (which components follow which)

The parser produces structured data ready to be converted into
Paradime custom integration nodes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes (plain dicts kept intentionally to avoid extra dependencies)
# ---------------------------------------------------------------------------

def _parse_component(name: str, definition: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise a single component definition into a flat dictionary.

    Args:
        name:       The component name as written in the YAML key.
        definition: The raw component dictionary from the YAML file.

    Returns:
        A normalised dictionary with the keys:
            - name            str   component display name
            - type            str   Matillion component type
            - skipped         bool  whether the component is marked skipped
            - endpoint        str | None   API endpoint (for extract components)
            - output_table    str | None   Snowflake target table name
            - transitions     list[str]    list of successor component names
    """
    params = definition.get("parameters", {})
    transitions_raw = definition.get("transitions", {})

    # Flatten all transition targets into a single list
    transitions: list[str] = []
    for _condition, targets in transitions_raw.items():
        if isinstance(targets, list):
            transitions.extend(targets)

    # Pull the Snowflake output connector block (if present)
    sf_output: dict[str, Any] = params.get("snowflake-output-connector-v0", {})
    output_table: str | None = sf_output.get("tableName") or None

    # Pull the API extract input block (if present)
    api_input: dict[str, Any] = params.get("api-extract-input-v2", {})
    endpoint: str | None = api_input.get("endpoint") or None

    return {
        "name": name,
        "type": definition.get("type", "unknown"),
        "skipped": bool(definition.get("skipped", False)),
        "endpoint": endpoint,
        "output_table": output_table,
        "transitions": transitions,
    }


def parse_matillion_yaml(file_path: Path) -> dict[str, Any]:
    """
    Parse a Matillion orchestration YAML file.

    Args:
        file_path: Path to the `.orch.yaml` file.

    Returns:
        A dictionary with the keys:
            - pipeline_name   str              derived from the file stem
            - description     str              pipeline description (or empty str)
            - components      list[dict]       list of parsed component dicts
    """
    logger.info(f"Parsing Matillion pipeline file: {file_path}")

    raw: dict[str, Any] = yaml.safe_load(file_path.read_text(encoding="utf-8"))

    pipeline_block: dict[str, Any] = raw.get("pipeline", {})

    if not pipeline_block:
        logger.warning(
            f"No 'pipeline' key found in {file_path}. "
            "Returning empty pipeline — check the YAML structure."
        )
        pipeline_name: str = (
            file_path.stem.replace(".orch", "").replace("_", " ").title()
        )
        return {"pipeline_name": pipeline_name, "description": "", "components": []}

    metadata_block: dict[str, Any] = pipeline_block.get("metadata", {})
    components_block: dict[str, Any] = pipeline_block.get("components", {})

    if not components_block:
        logger.warning(
            f"No 'components' key found in pipeline block of {file_path}. "
            "The pipeline will have no jobs."
        )

    description: str = metadata_block.get("description", "")

    # Derive a human-friendly pipeline name from the file stem.
    # e.g. "f1_custom_connector_load.orch" → "F1 Custom Connector Load"
    pipeline_name: str = (
        file_path.stem
        .replace(".orch", "")
        .replace("_", " ")
        .title()
    )

    components: list[dict[str, Any]] = []
    for comp_name, comp_def in components_block.items():
        parsed = _parse_component(comp_name, comp_def)
        components.append(parsed)
        logger.debug(
            f"  Component '{comp_name}' | type={parsed['type']} "
            f"| skipped={parsed['skipped']} | table={parsed['output_table']}"
        )

    logger.info(
        f"Parsed pipeline '{pipeline_name}' with {len(components)} components"
    )

    return {
        "pipeline_name": pipeline_name,
        "description": description,
        "components": components,
    }
