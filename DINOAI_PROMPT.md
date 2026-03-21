# DinoAI Prompt – Build a New Integration

Copy the prompt below into the DinoAI chat inside Paradime, fill in the
`[BRACKETED]` placeholders, and send it. DinoAI will read the existing
integrations for style reference and generate all six files for you.

> **Tip:** The more specific you are in sections **1, 3, and 4**, the less
> DinoAI has to guess — those three sections drive ~80% of the `parse.py`
> implementation.

---

## The Prompt

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

## Tips by Use Case

| If your tool has… | Add this to the prompt |
|---|---|
| **Nested hierarchies** (e.g. workspace → project → job) | List all 3+ levels in section 3 with what each represents |
| **Both upstream and downstream dbt links** | Describe both directions in section 4 — e.g. "reads from `stg_*` and writes back to `output_*`" |
| **A REST API instead of files** | Replace the file glob in section 2 with "REST API endpoint: `GET /v1/jobs`" and describe the response shape |
| **No GitHub source files** | Skip the repo URL / branch / glob — say "no source repo; data comes from env vars directly" |
| **Multiple file types** | List each glob and what it maps to (e.g. `*.pipeline.yaml` → Pipeline nodes, `*.job.yaml` → Job nodes) |

---

## What DinoAI Will Generate

| File | What changes vs. the template |
|------|-------------------------------|
| `integration.json` | `name` and `logo_url` filled in |
| `node_types.json` | Node types, icons, and colours chosen for your tool |
| `parse.py` | `parse_source_file()` and `convert_to_paradime_nodes()` fully implemented |
| `upload_to_paradime.py` | One-line filename change applied |
| `run_full_pipeline.py` | Display name updated |
| `README.md` | Full documentation written for your tool |

---

## Before You Open a PR

Work through the checklist in [CONTRIBUTING.md](CONTRIBUTING.md#6-checklist-before-opening-a-pr)
to make sure everything is production-ready.
