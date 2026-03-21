# DinoAI Prompt – Build a New Integration

Copy the prompt below into the DinoAI chat inside Paradime, fill in the
`[BRACKETED]` placeholders, and send it. DinoAI will read the existing
integrations for style reference and generate all files for you.

> **Tip:** The more specific you are in sections **1, 3, and 4**, the less
> DinoAI has to guess — those three sections drive ~80% of the implementation.

---

## Architecture Overview

Every integration follows a **two-layer architecture**:

```
paradime_custom_integration_api/
├── src/
│   └── parsers/
│       └── <your_tool>/          ← LAYER 1: pure parsing logic (library)
│           ├── __init__.py       ←   exports the public functions
│           └── parser.py         ←   all file/data parsing goes here, no orchestration
│
└── integrations/
    └── <your_tool>/              ← LAYER 2: orchestration (calls src, saves JSON)
        ├── integration.json
        ├── node_types.json
        ├── parse.py              ←   downloads repo, calls src parser, converts to nodes
        ├── upload_to_paradime.py
        ├── run_full_pipeline.py
        └── README.md
```

**Rules:**
- `src/parsers/<your_tool>/parser.py` contains **only** pure parsing functions — no file I/O, no network calls, no `sys.exit()`.
- `integrations/<your_tool>/parse.py` contains **only** orchestration — it imports from `src/` and converts parsed data into Paradime node dicts.
- `parse.py` adds the repo root to `sys.path` via `sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))` so it can import `from src.parsers.<your_tool>.parser import ...`.
- See `integrations/matillion/parse.py` and `src/parsers/matillion/parser.py` as the reference pair.

---

## The Prompt

```
I want to add a new Paradime custom integration for [TOOL NAME] to this repository.

Please read the following files for style reference BEFORE writing anything:

  Orchestration layer (template):
  - paradime_custom_integration_api/integrations/_template/parse.py
  - paradime_custom_integration_api/integrations/_template/upload_to_paradime.py
  - paradime_custom_integration_api/integrations/_template/run_full_pipeline.py

  Existing integration reference pair (orchestration + parser):
  - paradime_custom_integration_api/integrations/matillion/parse.py
  - paradime_custom_integration_api/src/parsers/matillion/parser.py
  - paradime_custom_integration_api/src/parsers/matillion/__init__.py

  Config examples:
  - paradime_custom_integration_api/integrations/matillion/integration.json
  - paradime_custom_integration_api/integrations/matillion/node_types.json

  Architecture rules:
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

### Layer 1 — Parser library (src/)

Create paradime_custom_integration_api/src/parsers/[your_tool]/parser.py:
  - Pure parsing functions only — no network calls, no sys.exit(), no file I/O beyond reading the input file
  - Implement `parse_source_file(file_path: Path) -> dict` with real parsing logic for [TOOL NAME] files
  - Include any helper functions (e.g. regex-based table extraction)
  - Full docstrings on every public function

Create paradime_custom_integration_api/src/parsers/[your_tool]/__init__.py:
  - Export the public functions from parser.py (e.g. `from .parser import parse_source_file`)

### Layer 2 — Orchestration (integrations/)

Create paradime_custom_integration_api/integrations/[your_tool]/integration.json:
  - name: "[Tool Display Name]"
  - logo_url: "[publicly hosted SVG/PNG URL — use a real one or leave the placeholder]"

Create paradime_custom_integration_api/integrations/[your_tool]/node_types.json:
  - One entry per node type defined in section 3
  - Choose appropriate Feather icon names (from feathericons.com) and valid Paradime colours:
    LEAF, CYAN, CORAL, VIOLET, ORANGE, MANDY, TEAL, GREEN

Create paradime_custom_integration_api/integrations/[your_tool]/parse.py:
  - Add sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent)) at the top
  - Import parse_source_file from src.parsers.[your_tool].parser
  - Copy `_find_repo_root()` and `download_repo()` verbatim from the _template — do NOT modify them
  - Replace [YOUR_ENV_PREFIX] with the prefix from section 5
  - Implement `convert_to_paradime_nodes()` using the lineage direction from section 4
    (all parsing logic must live in src/parsers/[your_tool]/parser.py, NOT here)
  - Use `output_file = target_dir / "[your_tool]_nodes.json"` from section 6
  - Update the source_files glob to match the file extension from section 2

Create paradime_custom_integration_api/integrations/[your_tool]/upload_to_paradime.py:
  - Copy from _template verbatim
  - Change only the ONE line: `nodes_file = ... / "[your_tool]_nodes.json"`

Create paradime_custom_integration_api/integrations/[your_tool]/run_full_pipeline.py:
  - Copy from _template verbatim
  - Change only the display name in the print() statement

Create paradime_custom_integration_api/integrations/[your_tool]/README.md:
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
| **Complex parsing logic** | Describe the parsing in detail in section 1 — DinoAI will put it in `src/parsers/[your_tool]/parser.py` |

---

## What DinoAI Will Generate

| File | Layer | What changes vs. the template |
|------|-------|-------------------------------|
| `src/parsers/<tool>/parser.py` | Parser (src) | All parsing logic for your file format implemented here |
| `src/parsers/<tool>/__init__.py` | Parser (src) | Exports the public parse functions |
| `integrations/<tool>/integration.json` | Orchestration | `name` and `logo_url` filled in |
| `integrations/<tool>/node_types.json` | Orchestration | Node types, icons, and colours chosen for your tool |
| `integrations/<tool>/parse.py` | Orchestration | Imports from `src/`, calls parser, converts to nodes, saves JSON |
| `integrations/<tool>/upload_to_paradime.py` | Orchestration | One-line filename change applied |
| `integrations/<tool>/run_full_pipeline.py` | Orchestration | Display name updated |
| `integrations/<tool>/README.md` | Orchestration | Full documentation written for your tool |

---

## Before You Open a PR

Work through the checklist in [CONTRIBUTING.md](CONTRIBUTING.md#6-checklist-before-opening-a-pr)
to make sure everything is production-ready.
