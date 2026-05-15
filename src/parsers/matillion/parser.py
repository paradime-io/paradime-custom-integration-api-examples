"""
Matillion Orchestration Pipeline Parser.

Parses Matillion `.orch.yaml` files and extracts:
  - Pipeline-level metadata (name, description, pipeline_type)
  - Component-level details (type, endpoint, output table, source table, skipped flag)
  - SQL-level lineage from ``sql-executor`` scripts (CREATE/INSERT/MERGE → table edges)
  - Cross-file orchestration calls (``run-orchestration`` → another `.orch.yaml`)
  - Shared-pipeline calls (``run-shared-pipeline``, e.g. dbt job invocations)
  - Transition graph (which components follow which)

Pipeline types
--------------
  ingestion    – SaaS → Snowflake (components with ``*-input-*`` types).
                 Jobs downstream to dbt source tables.
  reverse_etl  – Snowflake → SaaS (components with ``*-output`` types).
                 Jobs upstream from dbt models/views.
  orchestrator – Calls other pipelines via ``run-orchestration`` /
                 ``run-shared-pipeline``. Used for control-plane workflows.

The parser produces structured data ready to be converted into
Paradime custom integration nodes.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

logger = logging.getLogger(__name__)

# Matillion SQL scripts are riddled with ${VAR} substitutions (database,
# schema, audit-id, etc.). sqlglot can't parse those, so we replace each with
# a placeholder identifier before parsing — and then ignore the placeholder
# when collecting table names.
_MATILLION_VAR_PATTERN = re.compile(r"\$\{[^}]+\}")
_SQL_VAR_PLACEHOLDER = "_MTL_VAR_"


# ---------------------------------------------------------------------------
# Data classes (plain dicts kept intentionally to avoid extra dependencies)
# ---------------------------------------------------------------------------

def _component_role(component_type: str) -> str:
    """
    Classify a Matillion component type into a pipeline role.

    Rules (checked in order):
      - type contains ``-input-``       → ``ingestion_input``
      - type ends with ``-output``      → ``reverse_etl_output``
      - type == ``run-orchestration``   → ``orchestrator_call``
      - type == ``run-shared-pipeline`` → ``shared_pipeline_call``
      - type == ``sql-executor``        → ``sql_transform``
      - otherwise                       → ``utility``

    Args:
        component_type: Raw Matillion component type string.

    Returns:
        One of the role strings above.
    """
    t = component_type.lower()
    if "-input-" in t or t == "mongodb-query":
        return "ingestion_input"
    if t.endswith("-output"):
        return "reverse_etl_output"
    if t == "run-orchestration":
        return "orchestrator_call"
    if t == "run-shared-pipeline":
        return "shared_pipeline_call"
    if t == "sql-executor":
        return "sql_transform"
    return "utility"


def _strip_matillion_vars(sql: str) -> str:
    """Replace Matillion ``${VAR}`` substitutions with a parser-safe placeholder."""
    return _MATILLION_VAR_PATTERN.sub(_SQL_VAR_PLACEHOLDER, sql)


def _table_identifier(node: Any) -> str | None:
    """Extract the base table name from a sqlglot Table/Schema node."""
    if isinstance(node, exp.Schema):
        node = node.this
    if isinstance(node, exp.Table):
        return node.name or None
    return None


def _extract_sql_lineage(sql_script: str) -> tuple[list[str], list[str]]:
    """
    Extract source and target table names from a SQL script.

    Handles ``CREATE [OR REPLACE] [VIEW|TABLE] … AS SELECT``,
    ``INSERT INTO … SELECT``, ``UPDATE``, and ``MERGE INTO … USING``.
    Matillion ``${VAR}`` substitutions are replaced with a placeholder
    before parsing so sqlglot can read the statement.

    Returns:
        ``(sources, targets)`` — lowercase, deduplicated, sorted lists of
        the *base* table identifiers (db/schema qualifiers are dropped).
    """
    if not sql_script or not sql_script.strip():
        return [], []

    cleaned = _strip_matillion_vars(sql_script)

    try:
        statements = sqlglot.parse(cleaned, dialect="snowflake")
    except Exception as exc:
        logger.debug(f"  sqlglot failed to parse SQL — skipping lineage: {exc}")
        return [], []

    sources: set[str] = set()
    targets: set[str] = set()

    def _collect_sources_from(node: Any, exclude: set[str]) -> None:
        if node is None or not hasattr(node, "find_all"):
            return
        for tbl in node.find_all(exp.Table):
            name = tbl.name
            if name and name.lower() not in exclude:
                sources.add(name.lower())

    for stmt in statements:
        if stmt is None:
            continue

        if isinstance(stmt, exp.Create):
            tgt = _table_identifier(stmt.this)
            if tgt:
                targets.add(tgt.lower())
            _collect_sources_from(stmt.expression, exclude={tgt.lower()} if tgt else set())

        elif isinstance(stmt, exp.Insert):
            tgt = _table_identifier(stmt.this)
            if tgt:
                targets.add(tgt.lower())
            _collect_sources_from(stmt.expression, exclude={tgt.lower()} if tgt else set())

        # UPDATE is treated as state mutation (flag toggles, audit bookkeeping)
        # and intentionally skipped — real data movement uses INSERT/MERGE.

        elif isinstance(stmt, exp.Merge):
            tgt = _table_identifier(stmt.this)
            if tgt:
                targets.add(tgt.lower())
            using = stmt.args.get("using")
            _collect_sources_from(using, exclude={tgt.lower()} if tgt else set())

    placeholder = _SQL_VAR_PLACEHOLDER.lower()
    sources.discard(placeholder)
    targets.discard(placeholder)

    return sorted(sources), sorted(targets)


def _scalar_var_list_to_dict(raw: Any) -> dict[str, str]:
    """
    Convert Matillion's ``[[key, value], …]`` scalar-variable list into a dict.

    Matillion serialises name/value pairs as a list of two-element lists.
    Returns an empty dict if the input isn't in that shape.
    """
    if not isinstance(raw, list):
        return {}
    out: dict[str, str] = {}
    for entry in raw:
        if isinstance(entry, list) and len(entry) == 2:
            key, value = entry
            if key is not None:
                out[str(key)] = "" if value is None else str(value)
    return out


def _orchestration_job_leaf(orch_path: str | None) -> str | None:
    """
    Extract the leaf identifier from an ``orchestrationJob`` path.

    The value typically looks like
    ``DATA_SERVICES/Account360_Customer_Scorecard/A360_Customer_Scorecard_Orch``
    or ends in ``.orch.yaml`` — we return the final segment with any extension
    stripped (e.g. ``A360_Customer_Scorecard_Orch``). This matches the source
    file's stem so a second pass can resolve it to a Pipeline node.
    """
    if not orch_path:
        return None
    leaf = orch_path.rstrip("/").split("/")[-1]
    if leaf.lower().endswith(".orch.yaml"):
        leaf = leaf[: -len(".orch.yaml")]
    elif leaf.lower().endswith(".yaml"):
        leaf = leaf[: -len(".yaml")]
    return leaf or None


def _parse_component(name: str, definition: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise a single component definition into a flat dictionary.

    Args:
        name:       The component name as written in the YAML key.
        definition: The raw component dictionary from the YAML file.

    Returns:
        A normalised dictionary with the keys:
            - name                    str         component display name
            - type                    str         Matillion component type
            - role                    str         classification (see ``_component_role``)
            - skipped                 bool        whether the component is marked skipped
            - endpoint                str | None  API endpoint (for extract components)
            - output_table            str | None  Snowflake target table (ingestion destination)
            - source_table            str | None  Snowflake source table/view (reverse ETL source)
            - sql_sources             list[str]   tables read by a sql-executor script
            - sql_targets             list[str]   tables written by a sql-executor script
            - called_pipeline         str | None  leaf of ``orchestrationJob`` (run-orchestration)
            - called_pipeline_path    str | None  full ``orchestrationJob`` path
            - shared_pipeline_name    str | None  ``pipelineName`` (run-shared-pipeline)
            - shared_pipeline_vars    dict        scalar vars passed to a shared pipeline
            - transitions             list[str]   successor component names
    """
    params = definition.get("parameters", {})
    transitions_raw = definition.get("transitions", {})

    # Flatten all transition targets into a single list
    transitions: list[str] = []
    for _condition, targets in transitions_raw.items():
        if isinstance(targets, list):
            transitions.extend(targets)

    # Pull the Snowflake output connector block (ingestion: where data lands)
    sf_output: dict[str, Any] = params.get("snowflake-output-connector-v0", {})
    output_table: str | None = sf_output.get("tableName") or params.get("targetTable") or None

    # Pull the Snowflake source table (reverse ETL: where data is read from)
    source_table: str | None = params.get("sourceTable") or None

    # Pull the API extract input block (if present)
    api_input: dict[str, Any] = params.get("api-extract-input-v2", {})
    endpoint: str | None = api_input.get("endpoint") or None

    comp_type: str = definition.get("type", "unknown")

    # sql-executor: table-level lineage from the script body
    sql_sources: list[str] = []
    sql_targets: list[str] = []
    if comp_type == "sql-executor":
        sql_sources, sql_targets = _extract_sql_lineage(params.get("sqlScript", ""))

    # run-orchestration: cross-file call to another .orch.yaml
    called_pipeline_path: str | None = None
    called_pipeline: str | None = None
    if comp_type == "run-orchestration":
        called_pipeline_path = params.get("orchestrationJob")
        called_pipeline = _orchestration_job_leaf(called_pipeline_path)

    # run-shared-pipeline: call to a shared library pipeline (e.g. DBT_RUN_JOB)
    shared_pipeline_name: str | None = None
    shared_pipeline_vars: dict[str, str] = {}
    if comp_type == "run-shared-pipeline":
        shared_pipeline_name = params.get("pipelineName")
        shared_pipeline_vars = _scalar_var_list_to_dict(params.get("setScalarVariables"))

    return {
        "name": name,
        "type": comp_type,
        "role": _component_role(comp_type),
        "skipped": bool(definition.get("skipped", False)),
        "endpoint": endpoint,
        "output_table": output_table,
        "source_table": source_table,
        "sql_sources": sql_sources,
        "sql_targets": sql_targets,
        "called_pipeline": called_pipeline,
        "called_pipeline_path": called_pipeline_path,
        "shared_pipeline_name": shared_pipeline_name,
        "shared_pipeline_vars": shared_pipeline_vars,
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
            - pipeline_type   str              ``ingestion`` | ``reverse_etl`` | ``unknown``
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
        return {"pipeline_name": pipeline_name, "pipeline_type": "unknown", "description": "", "components": []}

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
        try:
            parsed = _parse_component(comp_name, comp_def)
        except Exception as exc:
            logger.warning(
                f"  Skipping component '{comp_name}' in {file_path.name} — "
                f"parse error: {exc}"
            )
            continue
        components.append(parsed)
        logger.debug(
            f"  Component '{comp_name}' | type={parsed['type']} "
            f"| role={parsed['role']} | skipped={parsed['skipped']} "
            f"| output_table={parsed['output_table']} | source_table={parsed['source_table']}"
        )

    # Determine pipeline type from the roles of its components.
    # Order matters: direct data movement (ingestion/reverse_etl) wins over
    # orchestration, which in turn wins over pure-SQL transforms.
    roles = {c["role"] for c in components}
    if "reverse_etl_output" in roles:
        pipeline_type = "reverse_etl"
    elif "ingestion_input" in roles:
        pipeline_type = "ingestion"
    elif "orchestrator_call" in roles or "shared_pipeline_call" in roles:
        pipeline_type = "orchestrator"
    elif "sql_transform" in roles:
        pipeline_type = "sql_transform"
    else:
        pipeline_type = "unknown"

    # Some pipelines land raw data into a _lnd table then merge it into a _stg
    # table via a Snowflake stored procedure called by a sql-executor. When this
    # pattern is detected, rewrite output tables to point at the staging layer
    # so lineage joins against the correct dbt source table.
    has_stg_merge = any(
        c["type"] == "sql-executor"
        and "_STG_MERGE" in (
            components_block.get(c["name"], {})
            .get("parameters", {})
            .get("sqlScript", "")
            .upper()
        )
        for c in components
    )
    if has_stg_merge:
        for c in components:
            if c["output_table"] and c["output_table"].upper().endswith("_LND"):
                c["output_table"] = c["output_table"][:-4] + "_STG"
        logger.info(
            f"  Staging merge detected — rewrote _LND → _STG for ingestion output tables"
        )

    # Warn about non-utility components that weren't classified — likely new
    # connector types that don't yet match the -input- / -output naming pattern
    unclassified = [
        c for c in components
        if c["role"] == "utility" and c["type"] not in {
            "start", "end-failure", "end-success", "or", "and",
            "retry", "query-to-scalar", "python-script",
            "iterator", "fixed-flow-iterator", "loop-iterator",
            "if", "case", "webhook-post",
        }
    ]
    for c in unclassified:
        logger.warning(
            f"  Unclassified component '{c['name']}' in {file_path.name} "
            f"(type='{c['type']}') — not recognised as ingestion or reverse ETL. "
            "Update _component_role() if this is a data-movement component."
        )

    logger.info(
        f"Parsed pipeline '{pipeline_name}' | type={pipeline_type} "
        f"| {len(components)} component(s)"
    )

    return {
        "pipeline_name": pipeline_name,
        "pipeline_type": pipeline_type,
        "description": description,
        "components": components,
    }
