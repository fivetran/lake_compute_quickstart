---
name: lake-compute-quickstart
description: >
  Run the Fivetran Lake Compute quickstart in this folder: dbt Fusion on the user's machine, Lake Compute
  building the Fivetran Platform dbt package next to their Managed Data Lake, results back in the lake as
  Iceberg tables, then queried with DuckDB. No warehouse or dbt platform.
  Trigger on: "lake compute quickstart", "set up lake compute", "try lake compute",
  "run dbt on my data lake", "transform my MDLS data".
allowed-tools: Bash(python3 scripts/lc.py:*) Bash(python scripts/lc.py:*) Bash(./bin/dbtw:*) Bash(./bin/lake:*)
---

# Lake Compute quickstart

Follow `AGENTS.md` at the root of this repository, step by step. It's the single set of instructions
for every AI assistant, so don't work from memory or from this file.

The two rules that matter most:
- Secrets go into `.env` in the user's own editor, never into the chat. Never read `.env` yourself.
- Dashboard steps (turning on Lake Compute, generating the token, adding the connector) are the
  user's. Tell them where to click, wait, then run the step's check.
