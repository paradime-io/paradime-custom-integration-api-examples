# Matillion Custom Integration for Paradime

This integration parses Matillion orchestration pipelines (`.orch.yaml` files) from a GitHub repository and creates Paradime SDK nodes showing lineage from Matillion load jobs through to dbt source models.

![Matillion lineage in Paradime](matillion_lineage.png)

## Features

- 📥 **Automatic Repo Download**: Downloads Matillion pipeline repos via ZIP (no Git required)
- 📂 **Local File Mode**: Parse a local `.orch.yaml` file directly — no GitHub download needed
- 🔍 **YAML Parsing**: Extracts pipeline metadata and all component definitions
- 🔄 **Pipeline Type Detection**: Automatically classifies each pipeline as **ingestion** (SaaS → Snowflake) or **reverse ETL** (Snowflake → destination)
- 🔗 **Lineage Tracking**: Ingestion jobs link downstream to dbt source tables; reverse ETL jobs link upstream from dbt models/views
- ⏭️ **Skipped Component Awareness**: Correctly flags components marked `skipped: true`
- 🎯 **Pipeline Filtering**: Process all pipelines or target specific ones via env var
- 🛡️ **Resilient Parsing**: Failed files are skipped with a warning and logged to `target/matillion_parse_failures.txt` — the run never stops mid-batch
- 📝 **Paradime SDK Format**: Outputs nodes ready for the Paradime custom integration API

---

## Node Types

### Pipeline Node
Represents the entire `.orch.yaml` orchestration file.

| Attribute     | Value                                                        |
|---------------|--------------------------------------------------------------|
| `name`        | Derived from the filename (e.g. `F1 Custom Connector Load`)  |
| `description` | From `pipeline.metadata.description`                        |
| `url`         | Direct link to the file on GitHub                            |

### Job Node
Represents a single non-Start component in the pipeline.

| Attribute     | Value                                                        |
|---------------|--------------------------------------------------------------|
| `name`        | `<Pipeline Name>.<Component Name>`                           |
| `description` | API endpoint called + Snowflake table written                |
| `url`         | Direct link to the `.orch.yaml` file on GitHub               |

---

## Lineage Model

### Ingestion pipelines (SaaS → Snowflake)

Components with `*-input-*` types (e.g. `modular-salesforce-input-v1`) pull data from a SaaS source and land it in a Snowflake table. The lineage connects Matillion jobs downstream to the dbt source tables that consume those landing tables.

```
Matillion Pipeline  (orchestrates)
    ↓
Matillion Job       (writes to Snowflake landing table)
    ↓
Snowflake Table     (e.g. CASE_LND)
    ↓
dbt Source          (references the landing table)
    ↓
dbt Staging / Mart Models
```

### Reverse ETL pipelines (Snowflake → destination)

Components with `*-output` types (e.g. `salesforce-output`) read from a Snowflake view or table produced by dbt and push data to an external destination. The lineage connects dbt models upstream into the Matillion job.

```
dbt Model / View    (e.g. SFDC_ACCOUNT360_CUSTOMER_SCORECARD_VW)
    ↓
Matillion Job       (reads from Snowflake, writes to destination)
    ↓
External Destination (e.g. Salesforce object)
```

---

## Configuration

All configuration is done via **environment variables** — no editing of `parse.py` required.

### Parsing Variables

| Variable                    | Required | Default                                                          | Description                                                                                          |
|-----------------------------|----------|------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| `MATILLION_LOCAL_FILE`      | No       | —                                                                | Absolute path to a local `.orch.yaml` file. When set (or passed as a CLI arg), skips the GitHub download entirely |
| `MATILLION_REPO_URL`        | No       | `https://github.com/paradime-sandbox/matillion-pipelines`        | URL of the GitHub repo containing `.orch.yaml` files                                                 |
| `MATILLION_BRANCH`          | No       | `main`                                                           | Git branch to download                                                                               |
| `MATILLION_PIPELINE_FILTER` | No       | *(empty — process all)*                                          | Comma-separated list of repo-relative `.orch.yaml` paths to process. Leave unset to process **all** |
| `GITHUB_TOKEN`              | No*      | —                                                                | GitHub PAT. Required for private repos                                                               |

> \* Anonymous download works for public repos. For private repos, `GITHUB_TOKEN` is required.

### Upload Variables

| Variable                | Required | Description                  |
|-------------------------|----------|------------------------------|
| `PARADIME_API_ENDPOINT` | Yes      | Paradime API endpoint URL    |
| `PARADIME_API_KEY`      | Yes      | Your Paradime API key        |
| `PARADIME_API_SECRET`   | Yes      | Your Paradime API secret     |

---

## Pipeline Filtering

By default the parser downloads the repo and processes **every** `.orch.yaml` file it finds.

To restrict to specific pipelines, set `MATILLION_PIPELINE_FILTER` to a comma-separated list of file paths **relative to the repo root**:

```bash
# Process a single pipeline
export MATILLION_PIPELINE_FILTER="f1_custom_connector_load.orch.yaml"

# Process multiple pipelines
export MATILLION_PIPELINE_FILTER="f1_api_load.orch.yaml,f1_custom_connector_load.orch.yaml"

# Process all pipelines (default — leave unset or empty)
unset MATILLION_PIPELINE_FILTER
```

If a path in the filter doesn't exist in the repo, it is skipped with a warning. If **none** of the filtered paths exist, the script exits with an error.

---

## Usage

### Prerequisites

1. Install dependencies:
   ```bash
   poetry install
   ```

2. Set environment variables:
   ```bash
   export GITHUB_TOKEN="ghp_your_token_here"          # for private repos
   export PARADIME_API_ENDPOINT="https://api.paradime.io"
   export PARADIME_API_KEY="your_api_key"
   export PARADIME_API_SECRET="your_api_secret"
   ```

3. *(Optional)* Configure the target repo and filter:
   ```bash
   export MATILLION_REPO_URL="https://github.com/your-org/your-matillion-repo"
   export MATILLION_BRANCH="main"
   export MATILLION_PIPELINE_FILTER="my_pipeline.orch.yaml"
   ```

### Option 1: Full Pipeline (Parse + Upload) — Recommended
```bash
cd integrations/matillion
poetry run python run_full_pipeline.py
```

### Option 2: Parse Only (generates `target/matillion_nodes.json`)
```bash
cd integrations/matillion
poetry run python parse.py
```

### Option 3: Parse a Local File (no GitHub download)
Pass the file path as a CLI argument or env var — useful for testing before committing to a repo:
```bash
# As a CLI argument
poetry run python integrations/matillion/parse.py /path/to/your/pipeline.orch.yaml

# As an environment variable
export MATILLION_LOCAL_FILE="/path/to/your/pipeline.orch.yaml"
poetry run python integrations/matillion/parse.py
```

### Option 4: Upload Only (requires existing `target/matillion_nodes.json`)
```bash
cd integrations/matillion
poetry run python upload_to_paradime.py
```

---

## Files

| File                    | Purpose                                                               |
|-------------------------|-----------------------------------------------------------------------|
| `integration.json`      | Integration name and logo                                             |
| `node_types.json`       | Node type definitions (Pipeline, Job)                                 |
| `parse.py`              | Main parsing script                                                   |
| `upload_to_paradime.py` | Uploads `target/matillion_nodes.json` to Paradime                     |
| `run_full_pipeline.py`  | Full parse + upload pipeline                                          |
| `run_integration.py`    | Parse-only runner (alias for `poetry run python parse.py`)                       |
| `README.md`             | This file                                                             |
| `QUICK_START.md`        | Minimal quick-start guide                                             |

> **Note:** The parser writes its output to `target/matillion_nodes.json` at the repo root. The `target/` directory is gitignored, so this file is never committed. Run `parse.py` (or `run_full_pipeline.py`) to regenerate it before uploading.

---

## Supported Component Types

The parser classifies components into three roles based on their Matillion type string:

| Role               | Type pattern       | Example                          | Effect                                      |
|--------------------|--------------------|----------------------------------|---------------------------------------------|
| `ingestion_input`  | contains `-input-` | `modular-salesforce-input-v1`    | `output_table` → downstream dbt source      |
| `reverse_etl_output` | ends with `-output` | `salesforce-output`            | `sourceTable` → upstream dbt model          |
| `utility`          | everything else    | `python-script`, `sql-executor`  | No lineage wiring                           |

Fields extracted per component:

| YAML Field                                           | Maps To                          |
|------------------------------------------------------|----------------------------------|
| `parameters.snowflake-output-connector-v0.tableName` | `output_table` (ingestion)       |
| `parameters.sourceTable`                             | `source_table` (reverse ETL)     |
| `parameters.api-extract-input-v2.endpoint`           | Job description                  |
| `skipped`                                            | Metadata flag                    |
| `transitions`                                        | (used for ordering)              |

If a component type is not recognised as ingestion, reverse ETL, or a known utility type, the parser logs a warning so you can extend `_component_role()` in `src/parsers/matillion/parser.py`.

---

## Troubleshooting

### "GITHUB_TOKEN not set"
```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

### "No .orch.yaml files found"
Verify `MATILLION_REPO_URL` and `MATILLION_BRANCH` point to the correct repo and branch.

### "None of the files in MATILLION_PIPELINE_FILTER were found"
Check that the paths in `MATILLION_PIPELINE_FILTER` are relative to the repo root (e.g. `f1_api_load.orch.yaml`, not `/f1_api_load.orch.yaml`).

### "Repository not found (404)"
- Check `MATILLION_REPO_URL` for typos.
- Confirm your `GITHUB_TOKEN` has `repo` scope for private repositories.

### "Paradime credentials missing"
```bash
export PARADIME_API_ENDPOINT="https://api.paradime.io"
export PARADIME_API_KEY="your_key"
export PARADIME_API_SECRET="your_secret"
```
