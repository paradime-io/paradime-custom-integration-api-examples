#!/usr/bin/env python3
"""
Upload Streamlit custom integration nodes to Paradime.

This script loads the generated nodes and uploads them to Paradime using the SDK.

Usage:
    python upload_to_paradime.py
    
Environment Variables:
    PARADIME_API_ENDPOINT: Paradime API endpoint
    PARADIME_API_KEY: Paradime API key
    PARADIME_API_SECRET: Paradime API secret
"""

# Standard library modules
import json
import logging
import sys
from pathlib import Path
from typing import Final, List

# Third party modules
from paradime.apis.custom_integration.types import Node, NodeType  # type: ignore
from paradime.tools.pydantic import BaseSettings, parse_obj_as  # type: ignore

# First party modules
from paradime import Paradime  # type: ignore

# Set up logging
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


# Load API credentials from environment variables
class ParadimeCredentials(BaseSettings):
    """Paradime API credentials loaded from environment variables."""
    
    api_endpoint: str
    api_key: str
    api_secret: str

    class Config:
        env_prefix = "PARADIME_"


def upload_integration_to_paradime(integration_dir: Path) -> None:
    """
    Upload a custom integration to Paradime.
    
    Args:
        integration_dir: Directory containing integration files
    """
    # Load credentials
    try:
        credentials = ParadimeCredentials()
        logger.info("✓ Loaded Paradime credentials from environment")
    except Exception as e:
        logger.error(f"✗ Failed to load Paradime credentials: {e}")
        logger.error("\nPlease set the following environment variables:")
        logger.error("  - PARADIME_API_ENDPOINT")
        logger.error("  - PARADIME_API_KEY")
        logger.error("  - PARADIME_API_SECRET")
        sys.exit(1)
    
    # Create Paradime client
    try:
        paradime = Paradime(
            api_endpoint=credentials.api_endpoint,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
        )
        logger.info("✓ Created Paradime client")
    except Exception as e:
        logger.error(f"✗ Failed to create Paradime client: {e}")
        sys.exit(1)
    
    # Resolve file paths
    # integration.json and node_types.json live alongside this script.
    # nodes.json is written to <repo_root>/target/ by parse.py.
    integration_file = integration_dir / "integration.json"
    node_types_file = integration_dir / "node_types.json"
    nodes_file = _find_repo_root(integration_dir) / "target" / "streamlit_nodes.json"

    # Check if files exist
    for file_path in [integration_file, node_types_file, nodes_file]:
        if not file_path.exists():
            logger.error(f"✗ Required file not found: {file_path}")
            sys.exit(1)
    
    try:
        # Load integration info
        integration_info = json.loads(integration_file.read_text())
        logger.info(f"✓ Loaded integration info: {integration_info.get('name')}")
        
        # Load node types
        node_types = parse_obj_as(
            List[NodeType], 
            json.loads(node_types_file.read_text())
        )
        logger.info(f"✓ Loaded {len(node_types)} node types")
        
        # Load nodes
        nodes = parse_obj_as(
            List[Node], 
            json.loads(nodes_file.read_text())
        )
        logger.info(f"✓ Loaded {len(nodes)} nodes")
        
    except Exception as e:
        logger.error(f"✗ Failed to load integration files: {e}")
        sys.exit(1)
    
    # Create or update the custom integration
    try:
        my_integration = paradime.custom_integration.upsert(
            name=integration_info.get("name"),
            logo_url=integration_info.get("logo_url"),
            node_types=node_types,
        )
        logger.info(f"✓ Created/updated integration: {my_integration.uid}")
    except Exception as e:
        logger.error(f"✗ Failed to upsert integration: {e}")
        sys.exit(1)
    
    # Add nodes to the custom integration
    try:
        paradime.custom_integration.add_nodes(
            integration_uid=my_integration.uid,
            nodes=nodes,
        )
        logger.info(f"✓ Added {len(nodes)} nodes to integration")
    except Exception as e:
        logger.error(f"✗ Failed to add nodes: {e}")
        sys.exit(1)
    
    logger.info("\n" + "=" * 60)
    logger.info("✓ Successfully uploaded integration to Paradime!")
    logger.info(f"  Integration: {integration_info.get('name')}")
    logger.info(f"  Node Types: {len(node_types)}")
    logger.info(f"  Nodes: {len(nodes)}")
    logger.info("=" * 60)


def main():
    """Main entry point."""
    logger.info("Paradime Custom Integration - Upload to Paradime")
    logger.info("=" * 60)
    
    # Get the integration directory (current directory)
    integration_dir = Path(__file__).parent
    
    # Upload the integration
    upload_integration_to_paradime(integration_dir)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n\nUpload cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n✗ Unexpected error: {e}")
        sys.exit(1)
