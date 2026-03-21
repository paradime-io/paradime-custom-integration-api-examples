#!/usr/bin/env python3
"""
Upload custom integration nodes to Paradime.

This script is identical across all integrations — copy it as-is.
The only thing that changes between integrations is the nodes filename
resolved inside _find_repo_root(), which is set in this file.

TODO: update the ``nodes_file`` line (~line 75) to match your integration name.

Usage:
    python upload_to_paradime.py

Environment Variables:
    PARADIME_API_ENDPOINT: Paradime API endpoint
    PARADIME_API_KEY:      Paradime API key
    PARADIME_API_SECRET:   Paradime API secret
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import List

from paradime.apis.custom_integration.types import Node, NodeType  # type: ignore
from paradime.tools.pydantic import BaseSettings, parse_obj_as  # type: ignore
from paradime import Paradime  # type: ignore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` to find the repo root (identified by dbt_project.yml)."""
    current = start.resolve()
    while True:
        if (current / "dbt_project.yml").exists():
            return current
        parent = current.parent
        if parent == current:
            logger.warning(
                "Could not locate dbt_project.yml — "
                "falling back to current working directory."
            )
            return Path.cwd()
        current = parent


class ParadimeCredentials(BaseSettings):
    """Paradime API credentials loaded from environment variables."""

    api_endpoint: str
    api_key: str
    api_secret: str

    class Config:
        env_prefix = "PARADIME_"


def upload_integration_to_paradime(integration_dir: Path) -> None:
    """
    Upload the custom integration to Paradime.

    Args:
        integration_dir: Directory containing the integration files.
    """
    # Load credentials
    try:
        credentials = ParadimeCredentials()
        logger.info("✓ Loaded Paradime credentials from environment")
    except Exception as exc:
        logger.error(f"✗ Failed to load Paradime credentials: {exc}")
        logger.error(
            "\nPlease set the following environment variables:\n"
            "  - PARADIME_API_ENDPOINT\n"
            "  - PARADIME_API_KEY\n"
            "  - PARADIME_API_SECRET"
        )
        sys.exit(1)

    # Create Paradime client
    try:
        paradime = Paradime(
            api_endpoint=credentials.api_endpoint,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
        )
        logger.info("✓ Created Paradime client")
    except Exception as exc:
        logger.error(f"✗ Failed to create Paradime client: {exc}")
        sys.exit(1)

    # Resolve file paths
    integration_file = integration_dir / "integration.json"
    node_types_file = integration_dir / "node_types.json"
    # TODO: rename [your_tool]_nodes.json to match your integration
    nodes_file = _find_repo_root(integration_dir) / "target" / "[your_tool]_nodes.json"

    for file_path in (integration_file, node_types_file, nodes_file):
        if not file_path.exists():
            logger.error(f"✗ Required file not found: {file_path}")
            sys.exit(1)

    try:
        integration_info = json.loads(integration_file.read_text())
        logger.info(f"✓ Loaded integration info: {integration_info.get('name')}")
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"✗ Failed to read integration.json: {exc}")
        sys.exit(1)

    try:
        node_types = parse_obj_as(
            List[NodeType],
            json.loads(node_types_file.read_text()),
        )
        logger.info(f"✓ Loaded {len(node_types)} node type(s)")
    except Exception as exc:
        logger.error(f"✗ node_types.json failed validation: {exc}")
        sys.exit(1)

    try:
        nodes = parse_obj_as(
            List[Node],
            json.loads(nodes_file.read_text()),
        )
        logger.info(f"✓ Loaded {len(nodes)} node(s)")
    except Exception as exc:
        logger.error(f"✗ nodes file failed validation: {exc}")
        sys.exit(1)

    # Upsert the integration definition
    try:
        my_integration = paradime.custom_integration.upsert(
            name=integration_info.get("name"),
            logo_url=integration_info.get("logo_url"),
            node_types=node_types,
        )
        logger.info(f"✓ Created/updated integration: {my_integration.uid}")
    except Exception as exc:
        logger.error(f"✗ Failed to upsert integration: {exc}")
        sys.exit(1)

    # Push nodes
    try:
        paradime.custom_integration.add_nodes(
            integration_uid=my_integration.uid,
            nodes=nodes,
        )
        logger.info(f"✓ Added {len(nodes)} node(s) to integration")
    except Exception as exc:
        logger.error(f"✗ Failed to add nodes: {exc}")
        sys.exit(1)

    logger.info(
        "\n"
        + "=" * 60 + "\n"
        + f"✓ Successfully uploaded to Paradime!\n"
        + f"  Integration : {integration_info.get('name')}\n"
        + f"  Node Types  : {len(node_types)}\n"
        + f"  Nodes       : {len(nodes)}\n"
        + "=" * 60
    )


def main() -> None:
    logger.info("Paradime Custom Integration – Upload to Paradime")
    logger.info("=" * 60)
    upload_integration_to_paradime(Path(__file__).parent)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nUpload cancelled by user.")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"\n✗ Unexpected error: {exc}")
        sys.exit(1)
