# Lake Compute quickstart: instructions for AI assistants

You're helping someone run Fivetran Lake Compute end to end on their own data. dbt Fusion runs on their
machine, Lake Compute runs the SQL next to their Managed Data Lake, and the results land back in the
lake as Iceberg tables. No dbt platform and no warehouse.

This folder is the project. Run every command from it. `README.md` has the same steps for people; keep
the two in step.

## Ground rules
- **Secrets never go in the chat or a command line.** The user types them into `.env` in their own
  editor. Read `.env` only through `scripts/lc.py`, `bin/dbtw` and `bin/lake`, which never print
  secrets. Never `cat`, `grep`, `echo` or "mask" `.env` yourself: a regex mask can fail and print the
  secret. If any output shows something matching `dct_[A-Za-z0-9_-]+`, redact it before repeating it.
- **Don't change the user's machine outside this folder** without asking. Don't edit
  `~/.dbt/profiles.yml` or shell rc files, and don't uninstall or replace an existing dbt.
- **Steps that need the Fivetran dashboard are the user's** (Lake Compute toggle, token, connector,
  read-only catalog secret). Tell them exactly where to click, wait
  until they say it's done, then run the step's check.
- **Each step ends with a check.** Don't move on until it passes. When a check fails, its message says
  what to fix. Pass that on in plain words.
- **Always build through the selector:** `./bin/dbtw build --selector fivetran_log`. Never an unselected
  `dbt build`.
- The user is evaluating the product. Explain what each step does in a sentence or two.

## Prerequisites (the user has these before starting)
- A Fivetran account.
- A Managed Data Lake destination in it.
- A Fivetran API key and secret (*Fivetran dashboard → user menu → API key*).

## Step 0: Credentials and destination
1. `cp .env.example .env && chmod 600 .env`
2. Ask the user to fill in `FIVETRAN_API_KEY` and `FIVETRAN_API_SECRET` in `.env` and save.
3. `python3 scripts/lc.py verify-key` shows the account and key owner. Ask the user to confirm.
4. `python3 scripts/lc.py destinations` lists Managed Data Lake destinations. Have the user pick one.
5. `python3 scripts/lc.py use-destination <group_id>` saves its catalog, endpoint, bucket and region to `.env`.

## Step 1: Install dbt Fusion
dbt Fusion is the dbt engine that can send models to Lake Compute.
1. `python3 scripts/lc.py fusion-check`. If it passes, go to step 2.
2. Otherwise install Fusion the way the dbt docs describe
   (https://docs.getdbt.com/docs/local/install-dbt?version=2). On macOS and Linux:
   `curl -fsSL https://public.cdn.getdbt.com/fs/install/install.sh | sh -s -- --update`, then `exec $SHELL`.
   **Skip `dbt login`**: this quickstart doesn't use the dbt platform.
   - The installer puts `dbt` in `~/.local/bin` and adds it to the PATH. If the user already has a `dbt`
     there they want to keep, use `python3 scripts/lc.py install-fusion` instead: it installs into
     `./bin` only. Ask which they prefer.
   - dbt Core (1.x) can't run Lake Compute. If it's installed, leave it; the quickstart won't use it.
3. `python3 scripts/lc.py fusion-check` again.

## Step 2: Turn on Lake Compute (dashboard)
Tell the user: *Fivetran dashboard → Destinations → (their destination) → switch Lake Compute on.*
Then `python3 scripts/lc.py lake-compute`.

## Step 3: Lake Compute token (dashboard)
Tell the user: *on the same destination page, Lake Compute → generate a token, paste it into
`LAKE_COMPUTE_TOKEN` in `.env` and save.* It starts with `dct_`. Remind them not to paste it in the chat.
Then `python3 scripts/lc.py token-check`. It confirms the token works and belongs to this destination.

## Step 4: Fivetran Platform connector
This free connector syncs the account's own activity (syncs, errors, usage, changes), so the demo uses
real data without touching business data.
1. `python3 scripts/lc.py log-connector`. If one already syncs into this destination, **use it**.
2. If there's none, tell the user: *Connectors → Add connector → Fivetran Platform → destination = theirs.*
   Then rerun the check.
3. If it has never finished a sync, `python3 scripts/lc.py log-connector --sync` starts one and waits.

## Step 5: Build on Lake Compute
1. `./bin/dbtw deps` installs the Fivetran Platform dbt package.
2. `./bin/dbtw parse` must finish cleanly. If it panics, see Troubleshooting.
3. `./bin/dbtw debug` checks the connection. **On a fresh lake its write test fails** with *"Schema …
   does not exist"*, and its last sentence says namespace creation isn't supported. Ignore both: `dbt
   debug` never creates schemas, and the build does. "Connection test passed" is what counts.
4. `python3 scripts/lc.py preflight` matches the package to the tables this account's connector really
   has. Show the user what it set and why.
5. `./bin/dbtw build --selector fivetran_log --threads 1` is the first build, about 5 minutes. One
   thread avoids a race when several models create the same new schema (`409 AlreadyExists`). If that
   happens anyway, rerun. Later builds can drop `--threads 1` (about 3 minutes).
   Expect 19 models and 8 tests, in schemas `quickstart_stg_fivetran_platform` and `quickstart_fivetran_platform`.
6. `python3 scripts/lc.py cost` shows Lake Compute's own record of the build: queries, data scanned,
   and estimated cost, next to a rough estimate for the same build on a typical cloud data warehouse.
   Run it straight after the build, before any other dbt command.

## Step 6: Query it yourself with DuckDB
DuckDB reads the results straight from the lake, with no Fivetran or dbt in between. That shows the
results are open Iceberg tables the user owns.
1. If `duckdb --version` shows 1.4 or later, it can be used as is. Otherwise
   `python3 scripts/lc.py install-duckdb` installs it into `./bin` only.
2. **Dashboard step.** Ask the user to open their destination's page in the Fivetran dashboard, copy the
   **read-only** catalog client secret, paste it into `POLARIS_CLIENT_SECRET` in `.env` and save. Reading
   is all this needs, and any user can see the read-only pair (only destination admins see the
   read/write one). The catalog address and client ID were saved in step 0.
3. `python3 scripts/lc.py duckdb-check` connects and lists the quickstart schemas.
4. `./bin/lake` opens a SQL prompt with the lake attached as `lake`.

## Step 7: Show what changed
Use `./bin/lake -c "<sql>"` and show the output.
1. **Before:** raw events, details packed into JSON.
   `select time_stamp, connection_id, event, message_event, message_data from lake.<LOG_SCHEMA>.log order by time_stamp desc limit 5`
2. **After:** one row per connector.
   `select connection_name, connector_type, connection_health, last_successful_sync_completed_at from lake.quickstart_fivetran_platform.fivetran_platform__connection_status order by connection_health, connection_name limit 10`
3. Tell them what else was built: `connection_daily_events`, `mar_table_history`, `usage_history`,
   `audit_table`, `audit_user_activity`, `schema_changelog`, each documented in the package.
4. Repeat the cost comparison from step 5.
5. Point them to "Read it from anywhere" in `README.md` for Snowflake, Python, Spark and Trino.

**Results look one sync behind.** The Fivetran Platform connector records each sync during the next
one, so the log connector's own latest sync shows up next time. The build isn't stale.

## Wrap-up
- **Rerun:** `./bin/dbtw build --selector fivetran_log`, then `python3 scripts/lc.py cost`.
- **Tear down:** drop `quickstart_fivetran_platform` and `quickstart_stg_fivetran_platform` from the
  catalog, delete this folder, and revoke the Lake Compute token on the destination page.

## Troubleshooting
- **`panic … Option::unwrap() on a None value` right after "Loading packages.yml":** a Fusion bug when
  Lake Compute is the only adapter and usage stats are on. `profiles.yml`'s `base_url` and
  `DBT_SEND_ANONYMOUS_USAGE_STATS=false` in `.env` both avoid it. Put back whichever was removed.
- **Panics write nothing to `logs/dbt.log`.** Rerun with `RUST_BACKTRACE=1 ./bin/dbtw <command>`.
- **`"lakecompute" adapter is not yet supported by dbt`:** `DBT_ALLOW_EXPERIMENTAL_ADAPTERS=true` is
  missing from `.env`, or dbt ran without `./bin/dbtw`.
- **`Catalog Error: Table with name … does not exist`** in a `stg_fivetran_platform__*` model: an optional
  source table is missing. Rerun `python3 scripts/lc.py preflight`, then build again.
- **Token check names another destination:** the token came from a different destination's page.
- **DuckDB `401` or `403`:** `POLARIS_CLIENT_SECRET` isn't this destination's read-only secret (for example,
  it's the read/write one, or from another destination).
