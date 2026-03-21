# Contributing – Building a New Integration

This guide explains how to add a new custom integration to this repository.
It covers the component anatomy, the manual approach, and a **DinoAI-powered
shortcut** that generates a working integration from a short prompt.

---

## Table of Contents

1. [Component Anatomy](#1-component-anatomy)
2. [Lineage Model Concepts](#2-lineage-model-concepts)
3. [Manual Step-by-Step](#3-manual-step-by-step)
4. [Using DinoAI to Generate an Integration](#4-using-dinoai-to-generate-an-integration)
5. [DinoAI Prompt Template](#5-dinoai-prompt-template)
6. [Checklist Before Opening a PR](#6-checklist-before-opening-a-pr)

---

## 1. Component Anatomy

Every integration is a folder under `integrations/` containing exactly these files:

```
integrations/my_tool/
├── integration.json        ← What Paradime calls this integration + its logo
├── node_types.json         ← What node categories exist (icon + colour per type)
├── parse.py                ← Downloads source repo, extracts data, writes nodes file
├── upload_to_paradime.py   ← Reads nodes file, pushes to Paradime SDK
├── run_full_pipeline.py    ← Orchestrates parse → upload in one command
└── README.md               ← User-facing documentation
```

### `integration.json`

Defines how the integration appears in the Paradime UI.

```json
{
  "name": "My Tool",
  "logo_url": "https://example.com/logo.svg"
}
```

| Field      | Description |
|------------|-------------|
| `name`     | Display name shown in Paradime. Must be unique across all integrations. |
| `logo_url` | Public URL to an SVG or PNG logo. GitHub Gists work well for hosting. |

---

### `node_types.json`

Defines the categories of nodes that exist in this integration.
Each type gets its own icon and colour in the Paradime lineage graph.

```json
[
  {
    "node_type": "Pipeline",
    "icon_name": "share-2",
    "color": "GREEN"
  },
  {
    "node_type": "Job",
    "icon_name": "database",
    "color": "ORANGE"
  }
]
```

| Field       | Description |
|-------------|-------------|
| `node_type` | Unique name for this type within the integration. Referenced in nodes. |
| `icon_name` | Feather icon name (e.g. `share-2`, `database`, `rocket`, `chart-bar`). Browse icons at [feathericons.com](https://feathericons.com). |
| `color`     | One of: `GREEN`, `ORANGE`, `CYAN`, `MANDY`, `BLUE`, `PURPLE`, `YELLOW`, `RED` |

---

### `parse.py`

The main script. It must:

1. **Download** the source repository as a ZIP archive (use `download_repo()` from `_template/parse.py` — it handles auth, retries, and cleanup).
2. **Find** the relevant source files (glob or filter via env var).
3. **Parse** each file and extract the data you need.
4. **Convert** the parsed data into Paradime node dictionaries (see [Lineage Model Concepts](#2-lineage-model-concepts)).
5. **Write** the result to `<repo_root>/target/<your_tool>_nodes.json`.

The `_find_repo_root()` helper (copy from `_template/parse.py`) locates the repo
root by walking up from `__file__` until `dbt_project.yml` is found — this keeps
all output paths dynamic and never hardcoded.

All user-facing configuration must be readable from **environment variables** with
sensible defaults. No hardcoded paths or credentials.

---

### `upload_to_paradime.py`

Reads the nodes file written by `parse.py` and calls the Paradime SDK:

```python
paradime.custom_integration.upsert(name, logo_url, node_types)
paradime.custom_integration.add_nodes(integration_uid, nodes)
```

**This file is identical across all integrations.** Copy it from `_template/` and
only change the `nodes_file` filename on the one line marked with `# TODO`.

---

### `run_full_pipeline.py`

Runs `parse.py` then `upload_to_paradime.py` via `subprocess`.
**Also identical across all integrations.** Copy from `_template/` and update the
display name in the `print()` line.

---

## 2. Lineage Model Concepts

### Node shape

```python
{
    "name": "My Pipeline.Load Drivers",   # unique within the integration
    "node_type": "Job",                   # must match a type in node_types.json
    "attributes": {
        "description": "Human-readable description.",
        "url": "https://github.com/org/repo/blob/main/file.yaml",
    },
    "lineage": {
        "upstream_dependencies":   [...],  # what feeds INTO this node
        "downstream_dependencies": [...],  # what this node feeds INTO
    },
}
```

### Dependency types

**Cross-node reference** (links two nodes within the same integration):
```python
{
    "integration_name": "My Tool",   # must match integration.json "name"
    "node_type": "Pipeline",         # must match a type in node_types.json
    "node_name": "My Pipeline",      # must match the "name" field of the target node
}
```

**dbt table reference** (creates lineage with dbt models):
```python
{
    "table_name": "f1_drivers"       # lower-cased Snowflake table name, no schema prefix
}
```

### Common lineage patterns

**Pattern A — Tool writes to Snowflake, dbt reads it (e.g. Matillion):**
```
Matillion Pipeline (upstream: nothing)
    ↓  [Pipeline upstream_dependencies: []]
Matillion Job      (upstream: Pipeline node)
    ↓  [Job downstream_dependencies: [{"table_name": "f1_drivers"}]]
Snowflake table    (resolved automatically by Paradime)
    ↓
dbt source → dbt staging → dbt mart
```

**Pattern B — dbt writes, tool reads (e.g. Streamlit):**
```
dbt model
    ↓
Streamlit Chart    (upstream: [{"table_name": "int_f1__race_results"}])
    ↓  [Chart downstream_dependencies: [{"integration_name": "Streamlit", ...App node}]]
Streamlit App      (upstream: nothing — App is the top-level container)
```

---

## 3. Manual Step-by-Step

1. **Copy the template folder:**
   ```bash
   cp -r paradime_custom_integration_api/integrations/_template \
         paradime_custom_integration_api/integrations/my_tool
   ```

2. **Fill in `integration.json`** — set `name` and `logo_url`.

3. **Fill in `node_types.json`** — define 1–4 node types with icons and colours.

4. **Implement `parse.py`:**
   - Replace `[YOUR_ENV_PREFIX]` throughout with e.g. `MYTOOL`
   - Implement `parse_source_file()` with your actual parsing logic
   - Implement `convert_to_paradime_nodes()` using the lineage patterns above
   - Update the `output_file` name from `[your_tool]_nodes.json`
   - Update `source_files` glob to match your file extension

5. **Update `upload_to_paradime.py`:**
   - Change the one `nodes_file` line to read `my_tool_nodes.json`

6. **Update `run_full_pipeline.py`:**
   - Update the `print()` display name

7. **Write `README.md`** using the template as a base.

8. **Test locally:**
   ```bash
   cd paradime_custom_integration_api/integrations/my_tool
   python parse.py          # check target/my_tool_nodes.json looks correct
   python run_full_pipeline.py
   ```

9. **Open a PR.**

---

## 4. Using DinoAI to Generate an Integration

[DinoAI](https://docs.paradime.io) is the AI assistant built into the Paradime
code editor. It understands this codebase, can read existing integrations for
context, and can generate a complete new integration from a short description.

### What DinoAI can do for you

| Task | What DinoAI produces |
|------|----------------------|
| Generate `integration.json` | Correct name + logo URL |
| Generate `node_types.json` | Appropriate node types, icons, and colours |
| Implement `parse.py` | Full parsing logic tailored to your source format |
| Copy + update `upload_to_paradime.py` | One-line filename change applied automatically |
| Copy + update `run_full_pipeline.py` | Display name updated |
| Write `README.md` | Full documentation matching the existing style |

### Tips for getting the best results

- **Be specific about your source format.** "YAML files with a `jobs:` key" is better
  than "config files".
- **Describe the lineage direction clearly** — does your tool feed dbt, or does dbt
  feed your tool?
- **Name your node types upfront** — DinoAI will use them consistently throughout all
  generated files.
- **Mention any env var naming conventions** you want (e.g. `MYTOOL_REPO_URL`).
- If your tool's source files are in a **private GitHub repo**, mention that so DinoAI
  includes `GITHUB_TOKEN` handling.

---

## 5. DinoAI Prompt Template

The full prompt lives in **[DINOAI_PROMPT.md](DINOAI_PROMPT.md)** — copy it into
the DinoAI chat, fill in the `[placeholders]`, and send it. DinoAI will read the
existing integrations for style reference and generate all six files for you.

For reference, the core prompt is reproduced below — the canonical version with
full instructions lives in **[DINOAI_PROMPT.md](DINOAI_PROMPT.md)**.

---

```
I want to add a new Paradime custom integration for [TOOL NAME] to this repository.

Please read the following files for style reference BEFORE writing anything:
- paradime_custom_integration_api/integrations/_template/parse.py
- paradime_custom_integration_api/integrations/_template/upload_to_paradime.py
- paradime_custom_integration_api/integrations/_template/run_full_pipeline.py
- paradime_custom_integration_api/integrations/matillion/parse.py
- paradime_custom_integration_api/integrations/matillion/integration.json
- paradime_custom_integration_api/integrations/matillion/node_types.json
- paradime_custom_integration_api/CONTRIBUTING.md

---

## 1. Source data format
[Describe the files / API responses your tool produces. Be specific.]
Examples:
  - "YAML files with a .pipeline.yaml extension — each file has a top-level `jobs:` list"
  - "Python files — I want to extract every pandas `read_sql()` or `to_sql()` call"
  - "JSON files at /v1/jobs from a REST API — each object has `id`, `name`, `tables`"

## 2. Source location
GitHub repo URL : [https://github.com/org/repo]
Branch          : [main]
File glob       : [e.g. **/*.pipeline.yaml]
Private repo    : [yes / no]

## 3. Node model
Define the node types (1–4) for this integration:
  - [NodeType1] : [what it represents, e.g. "one per .pipeline.yaml file"]
  - [NodeType2] : [what it represents, e.g. "one per job entry inside the file"]

## 4. Lineage direction
Choose one:
  Option A — Tool feeds dbt (tool writes Snowflake tables that dbt reads as sources):
    [NodeType2] sets `downstream_dependencies` to `{"table_name": "<snowflake_table>"}`.
    The table name is extracted from [describe where in the file the table name lives].

  Option B — dbt feeds tool (tool reads dbt model output):
    [NodeType2] sets `upstream_dependencies` to `{"table_name": "<dbt_model_name>"}`.
    The table name is extracted from [describe where in the file the table name lives].

## 5. Environment variable prefix
Use [YOUR_ENV_PREFIX] for all env vars (e.g. [YOUR_ENV_PREFIX]_REPO_URL, [YOUR_ENV_PREFIX]_BRANCH).

## 6. Output filename
Write nodes to: target/[your_tool]_nodes.json

---

## What I need you to build

Create the following files under paradime_custom_integration_api/integrations/[your_tool]/:

1. integration.json
   - name: "[Tool Display Name]"
   - logo_url: "[publicly hosted SVG/PNG URL — use a real one or leave the placeholder]"

2. node_types.json
   - One entry per node type defined in section 3
   - Choose appropriate Feather icon names (from feathericons.com) and Paradime colours
     (GREEN, ORANGE, CYAN, MANDY, BLUE, PURPLE, YELLOW, RED)

3. parse.py
   - Copy `_find_repo_root()` and `download_repo()` verbatim from the _template — do NOT modify them
   - Replace [YOUR_ENV_PREFIX] with the prefix from section 5
   - Implement `parse_source_file()` with real parsing logic for [TOOL NAME] files
   - Implement `convert_to_paradime_nodes()` using the lineage direction from section 4
   - Use `output_file = target_dir / "[your_tool]_nodes.json"` from section 6
   - Update the source_files glob to match the file extension from section 2

4. upload_to_paradime.py
   - Copy from _template verbatim
   - Change only the ONE line: `nodes_file = ... / "[your_tool]_nodes.json"`

5. run_full_pipeline.py
   - Copy from _template verbatim
   - Change only the display name in the print() statement

6. README.md
   - Follow the same structure as integrations/matillion/README.md
   - Replace all Matillion-specific content with [TOOL NAME] equivalents
   - Document every environment variable
   - Include the node type table, lineage diagram (ASCII is fine), and usage examples

After creating all files, show me a summary of what was generated and flag any
TODOs that still need manual input (e.g. a real logo URL, testing against a live repo).
```

---

## 6. Checklist Before Opening a PR

- [ ] `integration.json` has a unique `name` not used by any existing integration
- [ ] `logo_url` in `integration.json` is a publicly accessible URL
- [ ] `node_types.json` uses valid Feather icon names and Paradime colour values
- [ ] `parse.py` reads all config from environment variables (no hardcoded credentials)
- [ ] `parse.py` writes output to `target/<your_tool>_nodes.json` (not in the integration folder)
- [ ] `upload_to_paradime.py` references the correct nodes filename
- [ ] `temp_repo/` is cleaned up by the `finally` block in `parse.py`
- [ ] The integration folder name is added to `.gitignore` temp_repo entries if needed
- [ ] `python parse.py` runs without errors and produces a valid JSON file
- [ ] `python run_full_pipeline.py` runs end-to-end successfully
- [ ] `README.md` documents all environment variables, node types, and usage options
