# Paradime Custom Integration API – Examples

A collection of ready-to-use custom integrations for [Paradime](https://paradime.io) that
extend lineage graphs beyond dbt — connecting upstream data producers and downstream
consumers into a single, unified lineage view.

Each integration is self-contained: it downloads a source repository, parses the relevant
files, and pushes structured lineage nodes to Paradime via the
[Custom Integration API](https://docs.paradime.io/app-help/developers/python-sdk/modules/custom-integration).

---

## ✨ Build Your Own Integration

> **You can build a fully working custom integration in minutes** — no boilerplate to write from scratch.

The `integrations/_template/` folder contains a complete, heavily commented skeleton with
everything you need. Copy it, fill in your parsing logic, and you have a production-ready
integration.

### How the template works — and how you can extend it

The included template (and the Matillion / Streamlit reference integrations) follow a
**GitHub-repo-as-source** pattern: `parse.py` downloads your tool's config or definition
files directly from a GitHub repository as a ZIP archive, parses them locally, and
produces the lineage nodes file.

This is a great fit when your tool stores its configuration in code (e.g. YAML pipeline
files, Python app files, JSON job definitions). However, **you are not limited to this
approach**. The same integration structure works equally well with any other data source:

| Source pattern | How to adapt `parse.py` |
|---|---|
| **GitHub repo files** *(default template)* | Use the built-in `download_repo()` helper — no changes needed |
| **Third-party tool REST API** | Replace the `download_repo()` call with `requests.get()` / `httpx` calls to your tool's API endpoints |
| **Local files or a shared drive** | Point `source_files` at a local path instead of a `temp_repo/` directory |
| **Database / data warehouse query** | Query your warehouse directly (e.g. via `snowflake-connector-python`) and map the results to nodes |
| **Webhook / event stream** | Write a listener that feeds records into `convert_to_paradime_nodes()` on each event |

The `parse_source_file()` and `convert_to_paradime_nodes()` functions in the template are
the only parts you need to replace — everything else (`upload_to_paradime.py`,
`run_full_pipeline.py`, `integration.json`, `node_types.json`) stays the same regardless
of your data source.

---

### Two ways to get started

#### 🤖 Option 1 — Let DinoAI generate it for you (recommended)

Open **[DINOAI_PROMPT.md](DINOAI_PROMPT.md)**, copy the prompt template, fill in the
`[BRACKETED]` placeholders that describe your tool, and paste it into the DinoAI chat
inside Paradime. DinoAI will:

1. Read the existing integrations and `_template/` for style reference
2. Generate all **6 files** (`integration.json`, `node_types.json`, `parse.py`,
   `upload_to_paradime.py`, `run_full_pipeline.py`, `README.md`) under
   `integrations/<your_tool>/`
3. Flag any remaining TODOs (e.g. a real logo URL, live-repo testing)

> **Tip:** The more detail you give in the prompt's *source data format*, *node model*,
> and *lineage direction* sections, the less DinoAI has to guess — those three sections
> drive ~80% of the `parse.py` implementation.

#### 🛠️ Option 2 — Build it manually

Follow the step-by-step guide in [CONTRIBUTING.md](CONTRIBUTING.md):

```bash
# 1. Copy the template
cp -r paradime_custom_integration_api/integrations/_template \
      paradime_custom_integration_api/integrations/my_tool

# 2. Fill in integration.json, node_types.json
# 3. Implement parse.py (parsing logic + lineage mapping)
# 4. Update the one-line filename in upload_to_paradime.py
# 5. Update the display name in run_full_pipeline.py
# 6. Write README.md
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full anatomy of each file and the
pre-PR checklist.

---

## Available Integrations Examples

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
├── CONTRIBUTING.md                  ← How to build a new integration (full guide)
├── DINOAI_PROMPT.md                 ← Ready-to-use DinoAI prompt to generate an integration
├── pyproject.toml                   ← Shared Python dependencies
│
├── integrations/
│   │
│   ├── _template/                   ← ⭐ START HERE — copy this to build your own integration
│   │   ├── integration.json         ← Integration name + logo URL
│   │   ├── node_types.json          ← Node type definitions (icon, colour)
│   │   ├── parse.py                 ← Heavily commented parsing skeleton
│   │   ├── upload_to_paradime.py    ← SDK upload (copy as-is)
│   │   ├── run_full_pipeline.py     ← Parse + upload in one step (copy as-is)
│   │   └── README.md                ← Documentation template
│   │
│   ├── matillion/                   ← Example: Matillion integration (tool feeds dbt)
│   │   └── ...
│   │
│   └── streamlit/                   ← Example: Streamlit integration (dbt feeds tool)
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

## Quick Start (Run an Existing Integration)

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

| Path | What to do |
|------|------------|
| **Fastest** | Open [DINOAI_PROMPT.md](DINOAI_PROMPT.md), fill in the prompt, paste it into DinoAI — all 6 files are generated automatically |
| **Manual** | Follow the step-by-step in [CONTRIBUTING.md](CONTRIBUTING.md), using `integrations/_template/` as your starting point |

The existing [Matillion](integrations/matillion/README.md) and
[Streamlit](integrations/streamlit/README.md) integrations are fully working
reference implementations — read their `parse.py` files to see both lineage
directions (tool → dbt and dbt → tool) in practice.
