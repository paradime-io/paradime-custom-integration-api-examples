# [YOUR TOOL NAME] Custom Integration for Paradime

> **This is a template.** Replace every `[YOUR TOOL NAME]`, `[your_tool]`, and
> `[YOUR_ENV_PREFIX]` placeholder with your actual tool name before committing.

This integration parses [describe what your tool produces — e.g. "orchestration
pipeline YAML files"] and creates Paradime SDK nodes showing lineage from
[your tool] through to dbt models.

## Features

- 📥 **Automatic Repo Download**: Downloads source repos via ZIP (no Git required)
- 🔍 **File Parsing**: Extracts [describe what is extracted]
- 🔗 **Lineage Tracking**: Links [your tool] nodes to [upstream/downstream] dbt models
- 🎯 **File Filtering**: Process all files or target specific ones via env var
- 📝 **Paradime SDK Format**: Outputs nodes ready for the Paradime custom integration API

---

## Node Types

### ParentNode
Represents [describe the parent unit, e.g. a full pipeline file].

| Attribute     | Value                            |
|---------------|----------------------------------|
| `name`        | Derived from [describe source]   |
| `description` | From [describe source field]     |
| `url`         | Direct link to the file on GitHub |

### ChildNode
Represents [describe the child unit, e.g. a single job inside the pipeline].

| Attribute     | Value                            |
|---------------|----------------------------------|
| `name`        | `<ParentNode Name>.<Child Name>` |
| `description` | [describe what is shown]         |
| `url`         | Link to the source file on GitHub |

---

## Lineage Model

```
[Choose the direction that matches your tool]

Option A — tool feeds dbt:
[YOUR TOOL] ParentNode  →  ChildNode  →  Snowflake Table  →  dbt Source  →  dbt Models

Option B — dbt feeds tool:
dbt Model  →  ParentNode  →  ChildNode
```

---

## Configuration

All configuration is done via **environment variables** — no editing of `parse.py` required.

### Parsing Variables

| Variable                         | Required | Default                    | Description                                     |
|----------------------------------|----------|----------------------------|-------------------------------------------------|
| `[YOUR_ENV_PREFIX]_REPO_URL`     | No       | `https://github.com/...`   | URL of the GitHub repo containing source files  |
| `[YOUR_ENV_PREFIX]_BRANCH`       | No       | `main`                     | Git branch to download                          |
| `[YOUR_ENV_PREFIX]_FILE_FILTER`  | No       | *(process all files)*      | Comma-separated list of repo-relative file paths |
| `GITHUB_TOKEN`                   | No*      | —                          | GitHub PAT. Required for private repos          |

> \* Anonymous download works for public repos. For private repos, `GITHUB_TOKEN` is required.

### Upload Variables

| Variable                | Required | Description               |
|-------------------------|----------|---------------------------|
| `PARADIME_API_ENDPOINT` | Yes      | Paradime API endpoint URL |
| `PARADIME_API_KEY`      | Yes      | Your Paradime API key     |
| `PARADIME_API_SECRET`   | Yes      | Your Paradime API secret  |

---

## File Filtering

By default the parser processes **every** matching file in the repository.

To restrict to specific files, set `[YOUR_ENV_PREFIX]_FILE_FILTER`:

```bash
# Process a single file
export [YOUR_ENV_PREFIX]_FILE_FILTER="path/to/file.ext"

# Process multiple files
export [YOUR_ENV_PREFIX]_FILE_FILTER="file_a.ext,path/to/file_b.ext"

# Process all files (default)
unset [YOUR_ENV_PREFIX]_FILE_FILTER
```

---

## Usage

### Prerequisites

1. Install dependencies:
   ```bash
   cd paradime_custom_integration_api
   poetry install
   ```

2. Set environment variables:
   ```bash
   export GITHUB_TOKEN="ghp_your_token_here"
   export PARADIME_API_ENDPOINT="https://api.paradime.io"
   export PARADIME_API_KEY="your_api_key"
   export PARADIME_API_SECRET="your_api_secret"
   ```

3. *(Optional)* Configure the target repo and filter:
   ```bash
   export [YOUR_ENV_PREFIX]_REPO_URL="https://github.com/your-org/your-repo"
   export [YOUR_ENV_PREFIX]_BRANCH="main"
   export [YOUR_ENV_PREFIX]_FILE_FILTER="path/to/specific_file.ext"
   ```

### Option 1: Full Pipeline (Parse + Upload) — Recommended
```bash
cd paradime_custom_integration_api/integrations/[your_tool]
python run_full_pipeline.py
```

### Option 2: Parse Only (generates `target/[your_tool]_nodes.json`)
```bash
cd paradime_custom_integration_api/integrations/[your_tool]
python parse.py
```

### Option 3: Upload Only (requires existing `target/[your_tool]_nodes.json`)
```bash
cd paradime_custom_integration_api/integrations/[your_tool]
python upload_to_paradime.py
```

---

## Files

| File                    | Purpose                                                              |
|-------------------------|----------------------------------------------------------------------|
| `integration.json`      | Integration name and logo                                            |
| `node_types.json`       | Node type definitions (ParentNode, ChildNode)                        |
| `parse.py`              | Main parsing script                                                  |
| `upload_to_paradime.py` | Uploads `target/[your_tool]_nodes.json` to Paradime                  |
| `run_full_pipeline.py`  | Full parse + upload pipeline                                         |
| `README.md`             | This file                                                            |

> **Note:** The parser writes its output to `target/[your_tool]_nodes.json` at the
> repo root. The `target/` directory is gitignored — run `parse.py` (or
> `run_full_pipeline.py`) to regenerate it before uploading.

---

## Troubleshooting

### "GITHUB_TOKEN not set"
```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

### "No source files found"
Verify `[YOUR_ENV_PREFIX]_REPO_URL` and `[YOUR_ENV_PREFIX]_BRANCH` point to the
correct repo and branch.

### "None of the filtered files were found"
Check that the paths in `[YOUR_ENV_PREFIX]_FILE_FILTER` are relative to the repo
root (e.g. `pipelines/my_file.ext`, not `/pipelines/my_file.ext`).

### "Repository not found (404)"
- Check the repo URL for typos.
- Confirm your `GITHUB_TOKEN` has `repo` scope for private repositories.

### "Paradime credentials missing"
```bash
export PARADIME_API_ENDPOINT="https://api.paradime.io"
export PARADIME_API_KEY="your_key"
export PARADIME_API_SECRET="your_secret"
```
