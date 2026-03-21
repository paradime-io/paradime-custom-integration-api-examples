# Paradime Custom Integration API – Examples

A collection of ready-to-use custom integrations for [Paradime](https://paradime.io) that
extend lineage graphs beyond dbt — connecting upstream data producers and downstream
consumers into a single, unified lineage view.

Each integration is self-contained: it downloads a source repository, parses the relevant
files, and pushes structured lineage nodes to Paradime via the
[Custom Integration API](https://docs.paradime.io/app-help/developers/python-sdk/modules/custom-integration).

---

## Available Integrations

| Integration | What it parses | Lineage direction |
|-------------|----------------|-------------------|
| [Matillion](integrations/matillion/README.md) | `.orch.yaml` orchestration pipeline files | Matillion Job → Snowflake table → dbt source |
| [Streamlit](integrations/streamlit/README.md) | Streamlit `.py` app files | dbt model → Streamlit chart |

---

## Repository Structure

```
paradime_custom_integration_api/
│
├── README.md                        ← You are here
├── CONTRIBUTING.md                  ← How to build a new integration (+ DinoAI guide)
├── pyproject.toml                   ← Shared Python dependencies
│
├── integrations/
│   │
│   ├── _template/                   ← Copy this folder to start a new integration
│   │   ├── integration.json         ← Integration name + logo URL
│   │   ├── node_types.json          ← Node type definitions (icon, colour)
│   │   ├── parse.py                 ← Heavily commented parsing skeleton
│   │   ├── upload_to_paradime.py    ← SDK upload (copy as-is)
│   │   ├── run_full_pipeline.py     ← Parse + upload in one step (copy as-is)
│   │   └── README.md                ← Documentation template
│   │
│   ├── matillion/                   ← Matillion integration
│   │   └── ...
│   │
│   └── streamlit/                   ← Streamlit integration
│       └── ...
│
└── src/                             ← Shared parsing utilities
    └── parsers/
        ├── matillion/
        │   └── parser.py            ← Matillion YAML parser
        └── streamlit/
            └── parser.py            ← Streamlit SQL/AST parser
```

---

## How It Works

Every integration follows the same three-step pattern:

```
1. parse.py
   └─ Downloads source repo as ZIP
   └─ Parses files (YAML / Python / JSON / etc.)
   └─ Writes  →  target/<integration>_nodes.json

2. upload_to_paradime.py
   └─ Reads   ←  target/<integration>_nodes.json
   └─ Calls Paradime SDK: upsert integration + add nodes

3. run_full_pipeline.py
   └─ Runs parse.py then upload_to_paradime.py in sequence
```

The `target/` directory at the repo root is **gitignored** — nodes files are
regenerated on every run and are never committed.

---

## Lineage Node Anatomy

Every node sent to Paradime has this shape:

```json
{
  "name": "My Pipeline.Load Drivers",
  "node_type": "Job",
  "attributes": {
    "description": "Calls the /drivers API and writes to F1_DRIVERS table.",
    "url": "https://github.com/org/repo/blob/main/pipeline.yaml"
  },
  "lineage": {
    "upstream_dependencies": [
      {
        "integration_name": "Matillion",
        "node_type": "Pipeline",
        "node_name": "My Pipeline"
      }
    ],
    "downstream_dependencies": [
      { "table_name": "f1_drivers" }
    ]
  }
}
```

**Dependency types:**

| Type | Shape | When to use |
|------|-------|-------------|
| Cross-node (same integration) | `{"integration_name": "...", "node_type": "...", "node_name": "..."}` | Link a Job to its parent Pipeline |
| dbt table reference | `{"table_name": "lower_case_table_name"}` | Link to a Snowflake table read/written by dbt |

---

## Quick Start

```bash
# 1. Install dependencies
cd paradime_custom_integration_api
poetry install

# 2. Set credentials
export GITHUB_TOKEN="ghp_your_token"
export PARADIME_API_ENDPOINT="https://api.paradime.io"
export PARADIME_API_KEY="your_key"
export PARADIME_API_SECRET="your_secret"

# 3. Run an integration
cd integrations/matillion
python run_full_pipeline.py
```

See each integration's `README.md` and `QUICK_START.md` for full details.

---

## Adding a New Integration

See [CONTRIBUTING.md](CONTRIBUTING.md) for a step-by-step guide, or jump straight
to [DINOAI_PROMPT.md](DINOAI_PROMPT.md) for a ready-to-use prompt that generates
a complete new integration automatically.
