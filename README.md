# Fivetran Lake Compute quickstart

Transform your Fivetran data where it already lives. In about 30 minutes you'll have dbt Fusion running
on your laptop, sending models to **Fivetran Lake Compute**, which runs the SQL next to your Managed
Data Lake and writes the results back to it as open Iceberg tables. No warehouse and no dbt platform
account.

You'll use the Fivetran Platform connector (your account's own sync and usage logs) as the data, and
the Fivetran Platform dbt package to turn those raw logs into tables you can put in front of a
dashboard or an AI assistant. Then you'll query the results with DuckDB, straight from your lake.

> **Draft.** This quickstart is being tested. Expect changes.

## What you need
- A Fivetran account
- A Managed Data Lake destination in that account
- A Fivetran API key and secret (*Fivetran dashboard → your user menu → API key*)
- macOS or Linux with Python 3.9 or later

## With an AI assistant
Open this folder in your AI coding assistant (Claude Code, Cursor, Codex, GitHub Copilot or similar)
and ask it to **run the Lake Compute quickstart**. It follows [`AGENTS.md`](AGENTS.md), runs the checks,
and tells you when a step needs you in the Fivetran dashboard. In Claude Code you can also type
`/lake-compute-quickstart`.

Your API secret and tokens go into `.env` in your own editor. You never paste them into the chat.

## By hand
Run everything from this folder.

### 0. Credentials and destination
```sh
cp .env.example .env && chmod 600 .env
```
Open `.env` and fill in `FIVETRAN_API_KEY` and `FIVETRAN_API_SECRET`. Then:
```sh
python3 scripts/lc.py verify-key                 # confirms the account and key owner
python3 scripts/lc.py destinations               # lists your Managed Data Lake destinations
python3 scripts/lc.py use-destination <group_id> # saves the one you pick to .env
```

### 1. Install dbt Fusion
Follow the [dbt Fusion install guide](https://docs.getdbt.com/docs/local/install-dbt?version=2). On
macOS and Linux:
```sh
curl -fsSL https://public.cdn.getdbt.com/fs/install/install.sh | sh -s -- --update
exec $SHELL
```
You don't need `dbt login`. Then check it:
```sh
python3 scripts/lc.py fusion-check
```
Already have a `dbt` you'd rather not replace? `python3 scripts/lc.py install-fusion` installs Fusion
into `./bin` instead, and the quickstart uses that one. dbt Core (1.x) can stay installed, but it can't
run Lake Compute.

### 2. Turn on Lake Compute
In the Fivetran dashboard, open **Destinations → your Managed Data Lake destination** and switch
**Lake Compute** on. Then:
```sh
python3 scripts/lc.py lake-compute
```

### 3. Create a Lake Compute token
On the same destination page, go to **Lake Compute** and generate a token. Paste it into
`LAKE_COMPUTE_TOKEN` in `.env` and save. Then:
```sh
python3 scripts/lc.py token-check
```

### 4. Connect the Fivetran Platform connector
If a Fivetran Platform connector already syncs into this destination, the quickstart uses it.
Otherwise add one: **Connectors → Add connector → Fivetran Platform**, with your Managed Data Lake as
the destination. Then:
```sh
python3 scripts/lc.py log-connector          # finds it
python3 scripts/lc.py log-connector --sync   # only if it has never finished a sync
```

### 5. Build on Lake Compute
```sh
./bin/dbtw deps
./bin/dbtw parse
./bin/dbtw debug                     # on a new lake, the write test fails; "connection test passed" is what counts
python3 scripts/lc.py preflight      # matches the package to the tables your connector has
./bin/dbtw build --selector fivetran_log
python3 scripts/lc.py cost           # queries, data scanned, estimated cost
```
The build takes about 3 minutes and creates 19 models in two new schemas,
`quickstart_stg_fivetran_platform` and `quickstart_fivetran_platform`. If a model fails with
`409 AlreadyExists` on the first build, rerun it once.

`lc.py cost` shows Lake Compute's estimated cost for the build, from its own record of every query, next
to a rough estimate for running the same build on a typical cloud data warehouse.

### 6. Query it yourself with DuckDB
DuckDB needs read access to your catalog. In the Fivetran dashboard, open your destination's page,
copy the **read-only** client secret, and paste it into `POLARIS_CLIENT_SECRET` in `.env`. The catalog
address and client ID are already there from step 0. Then:
```sh
python3 scripts/lc.py install-duckdb   # into ./bin only; skip if you have DuckDB 1.4 or later
python3 scripts/lc.py duckdb-check
./bin/lake                             # SQL prompt, with your lake attached as `lake`
```

### 7. See what changed
Before: raw sync events, with the details packed into JSON.
```sql
select time_stamp, connection_id, event, message_data
from lake.<your log schema>.log order by time_stamp desc limit 5;
```
After: one row per connector, with its health and last successful sync.
```sql
select connection_name, connector_type, connection_health, last_successful_sync_completed_at
from lake.quickstart_fivetran_platform.fivetran_platform__connection_status
order by connection_health, connection_name;
```
The build also created `connection_daily_events`, `mar_table_history`, `usage_history`,
`audit_table`, `audit_user_activity` and `schema_changelog`, each with documented columns.

The Fivetran Platform connector records each sync during the next one, so its own latest sync shows
up next time.

## Read it from anywhere
The results are Iceberg tables in your destination's catalog. Any engine that speaks the Iceberg REST
catalog can read them, using the same read-only credentials. Your catalog endpoint, name and client ID
are in `.env`.

**DuckDB:** `./bin/lake` does this for you. The setup it runs is in [`scripts/lake.sql`](scripts/lake.sql).

**Python (PyIceberg):**
```python
# pip install "pyiceberg[pyarrow,pandas]"
import os
from pyiceberg.catalog import load_catalog

lake = load_catalog("lake", type="rest",
    uri=os.environ["POLARIS_ENDPOINT"], warehouse=os.environ["LC_CATALOG"],
    credential=f"{os.environ['POLARIS_CLIENT_ID']}:{os.environ['POLARIS_CLIENT_SECRET']}",
    scope="PRINCIPAL_ROLE:ALL")
df = lake.load_table("quickstart_fivetran_platform.fivetran_platform__connection_status").scan().to_pandas()
```

**Snowflake:** create a catalog integration for the Iceberg REST catalog and a catalog-linked
database, or add a Fivetran destination that keeps Snowflake in sync with your lake.

**Spark, Trino, Athena:** configure an Iceberg REST catalog with the same endpoint, warehouse
(your catalog name) and client credentials.

## Start over or clean up
- Rerun: `./bin/dbtw build --selector fivetran_log`
- Clean up: drop the two `quickstart_*` schemas from your catalog, delete this folder, and revoke the
  Lake Compute token on the destination page.

## Troubleshooting
See the Troubleshooting section in [`AGENTS.md`](AGENTS.md#troubleshooting).
