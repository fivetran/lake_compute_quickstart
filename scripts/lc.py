#!/usr/bin/env python3
"""Lake Compute quickstart helper. Standard library only. Never prints secrets.

Every command works from any folder: it changes to the quickstart folder (the one holding .env) first.

  Step 0  python3 scripts/lc.py verify-key               who owns the Fivetran key, which account
          python3 scripts/lc.py destinations             Managed Data Lake destinations, Lake Compute on/off
          python3 scripts/lc.py use-destination <id>     saves the destination's details to .env
  Step 1  python3 scripts/lc.py fusion-check             is dbt Fusion installed (and not dbt Core)?
          python3 scripts/lc.py install-fusion           alternative: latest dbt Fusion into ./bin only
  Step 2  python3 scripts/lc.py lake-compute             is Lake Compute switched on for the destination?
  Step 3  python3 scripts/lc.py token-check              Lake Compute token: valid, and for this destination?
  Step 4  python3 scripts/lc.py log-connector [--sync]   find the Fivetran Platform connector; optionally sync
  Step 5  python3 scripts/lc.py preflight                set package vars from the tables the connector has
          python3 scripts/lc.py cost                     what the last build cost on Lake Compute
  Step 6  python3 scripts/lc.py install-duckdb           latest DuckDB into ./bin only
          python3 scripts/lc.py duckdb-check             can DuckDB read your catalog directly?

Secrets (FIVETRAN_API_SECRET, LAKE_COMPUTE_TOKEN, POLARIS_CLIENT_SECRET) are read from .env only and are
never printed. Only non-secret fields are shown.
"""
import base64
import io
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
API = "https://api.fivetran.com/v1"
TOKEN_EXCHANGE = "https://api.fivetran.com/partner/v1/dbt-compute/token-exchange"
FUSION_CDN = "https://public.cdn.getdbt.com/fs"
# Pinned to the version tested against Polaris with ./bin/lake. Bump after retesting step 6.
DUCKDB_VERSION = "v1.5.0"
PROJECT = "lake_compute_quickstart"  # dbt_project.yml name; Lake Compute records it on every query
SECRET_RE = re.compile(r"dct_[A-Za-z0-9_-]+")


def die(msg):
    print(f"ERROR: {redact(msg)}")
    sys.exit(1)


def redact(text):
    text = SECRET_RE.sub("dct_***", str(text))
    for k in ("FIVETRAN_API_SECRET", "LAKE_COMPUTE_TOKEN", "POLARIS_CLIENT_SECRET"):
        v = _ENV_CACHE.get(k)
        if v and len(v) > 4:
            text = text.replace(v, "***")
    return text


_ENV_CACHE = {}


def load_env():
    if not ENV.exists():
        die(".env not found. Copy .env.example to .env and fill in your Fivetran API key and secret.")
    if git_tracks(".env"):
        die(".env is tracked by git. Run `git rm --cached .env` so your secrets aren't committed.")
    env = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    _ENV_CACHE.update(env)
    return env


def git_tracks(path):
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT, capture_output=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def save_env(updates):
    """Set keys in .env, keeping every other line (and the file mode) unchanged."""
    lines, seen = ENV.read_text().splitlines(), set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if k in updates:
            lines[i], seen = f"{k}={updates[k]}", seen | {k}
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    ENV.write_text("\n".join(lines) + "\n")
    os.chmod(ENV, 0o600)
    _ENV_CACHE.update(updates)


def need(env, *keys):
    missing = [k for k in keys if not env.get(k)]
    if missing:
        die(f"fill in {', '.join(missing)} in .env first")


def fivetran(env, method, path, body=None):
    need(env, "FIVETRAN_API_KEY", "FIVETRAN_API_SECRET")
    auth = base64.b64encode(f"{env['FIVETRAN_API_KEY']}:{env['FIVETRAN_API_SECRET']}".encode()).decode()
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Basic {auth}", "Accept": "application/json",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"code": f"HTTP {e.code}", "message": e.reason}


def destination(env, group_id):
    d = fivetran(env, "GET", f"/destinations/{group_id}").get("data")
    if not d or d.get("service") != "managed_data_lake":
        die(f"{group_id} is not a Managed Data Lake destination")
    return d


# ---- Step 0: prerequisites -------------------------------------------------------------------------

def verify_key():
    env = load_env()
    info = fivetran(env, "GET", "/account/info")
    if "data" not in info:
        die(f"key rejected: {info.get('code')} {info.get('message')}")
    d = info["data"]
    user = fivetran(env, "GET", f"/users/{d['user_id']}").get("data", {})
    print(f"account: {d.get('account_name')} ({d.get('account_id')})")
    print(f"key owner: {user.get('given_name', '')} {user.get('family_name', '')} <{user.get('email')}>")


def destinations():
    env = load_env()
    groups = fivetran(env, "GET", "/groups?limit=1000").get("data", {}).get("items", [])
    found = 0
    for g in groups:
        d = fivetran(env, "GET", f"/destinations/{g['id']}").get("data")
        if not d or d.get("service") != "managed_data_lake":
            continue  # groups without a destination, or a different destination type
        c, pc = d.get("config", {}), d.get("config", {}).get("polaris_catalog_configuration", {})
        found += 1
        print(f"{g['id']}  name={g.get('name')!r}  region={d.get('region')}  "
              f"lake_compute={'ON' if c.get('is_dbt_compute_enabled') else 'off'}  "
              f"catalog={pc.get('polaris_catalog')}  bucket={c.get('bucket')}/{c.get('prefix_path') or ''}")
    if not found:
        print("No Managed Data Lake destinations in this account. Create one in the Fivetran dashboard first.")


def use_destination(group_id):
    env = load_env()
    c = destination(env, group_id)["config"]
    pc = c.get("polaris_catalog_configuration", {})
    updates = {"FIVETRAN_GROUP_ID": group_id, "LC_CATALOG": pc.get("polaris_catalog", ""),
               "POLARIS_ENDPOINT": (pc.get("polaris_server_endpoint") or "").rstrip("/"),
               "MDLS_BUCKET": c.get("bucket", ""), "MDLS_PREFIX": c.get("prefix_path", ""),
               "MDLS_REGION": c.get("region", "")}
    # The catalog client ID and secret only work as a pair. The API masks the secret, so fill in the ID only
    # when the user hasn't entered a pair of their own, and save the secret only if the API returns it.
    secret = pc.get("client_secret") or ""
    if not env.get("POLARIS_CLIENT_SECRET"):
        updates["POLARIS_CLIENT_ID"] = pc.get("client_id", "")
        if secret and set(secret) != {"*"}:
            updates["POLARIS_CLIENT_SECRET"] = secret  # written to .env only, never printed
    save_env(updates)
    print(f"saved destination {group_id} (catalog {pc.get('polaris_catalog')}) to .env")
    print(f"Lake Compute: {'ON' if c.get('is_dbt_compute_enabled') else 'off (step 2 switches it on)'}")


# ---- Step 1: dbt Fusion ----------------------------------------------------------------------------

def find_dbt():
    local = ROOT / "bin" / "dbt"
    return str(local) if local.exists() else shutil.which("dbt")


def fusion_check():
    dbt = find_dbt()
    if not dbt:
        die("dbt Fusion isn't installed. Install it (step 1), then rerun this check.")
    out = subprocess.run([dbt, "--version"], capture_output=True, text=True, timeout=60)
    first = (out.stdout + out.stderr).strip().splitlines()[:1]
    version = first[0] if first else ""
    if re.match(r"dbt(-fusion)? 2\.", version):
        print(f"dbt Fusion found: {version}  ({dbt})")
        return
    if "Core" in out.stdout + out.stderr:
        die(f"{dbt} is dbt Core, which can't run Lake Compute. Install dbt Fusion (step 1). "
            "Your dbt Core install can stay; the quickstart won't use it.")
    die(f"{dbt} doesn't look like dbt Fusion (version output: {version!r})")


def install_fusion():
    """Latest released dbt Fusion into ./bin. An alternative to dbt's installer, which installs into
    ~/.local/bin (replacing any dbt there) and adds it to your PATH."""
    machine, system = platform.machine().lower(), platform.system()
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(machine)
    target = {"Darwin": f"{arch}-apple-darwin",
              "Linux": f"{arch}-unknown-linux-gnu"}.get(system)  # TODO: confirm the Linux target name
    if not arch or not target:
        die(f"unsupported platform {system}/{machine}")
    with urllib.request.urlopen(f"{FUSION_CDN}/versions.json", timeout=60) as r:
        tag = json.load(r)["latest"]["tag"]  # e.g. v2.0.9
    url = f"{FUSION_CDN}/cli/fs-{tag}-{target}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        tgz = pathlib.Path(tmp) / "fusion.tar.gz"
        urllib.request.urlretrieve(url, tgz)
        with tarfile.open(tgz) as t:
            if sys.version_info >= (3, 11, 4):
                t.extractall(tmp, filter="data")
            else:  # older Python has no extraction filter: validate member paths ourselves
                root = pathlib.Path(tmp).resolve()
                for m in t.getmembers():
                    if m.issym() or m.islnk() or not (root / m.name).resolve().is_relative_to(root):
                        die(f"refusing suspicious archive member {m.name!r}")
                t.extractall(tmp)
        found = [p for p in pathlib.Path(tmp).rglob("dbt") if p.is_file()]
        if not found:
            die(f"no dbt binary in {url}")
        dest = install_binary(found[0].read_bytes(), "dbt")
    print(f"installed dbt Fusion {tag} ({target}) to {dest}")


def install_binary(data, name):
    bindir = ROOT / "bin"
    bindir.mkdir(exist_ok=True)
    dest = bindir / name
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    dest.write_bytes(data)
    os.chmod(dest, 0o755)
    return dest.relative_to(ROOT)


# ---- Steps 2 and 3: Lake Compute -------------------------------------------------------------------

def lake_compute():
    env = load_env()
    need(env, "FIVETRAN_GROUP_ID")
    c = destination(env, env["FIVETRAN_GROUP_ID"])["config"]
    if not c.get("is_dbt_compute_enabled"):
        die(f"Lake Compute is off for destination {env['FIVETRAN_GROUP_ID']}. In the Fivetran dashboard, open "
            "Destinations -> your Managed Data Lake destination and switch Lake Compute on. Then rerun.")
    print(f"Lake Compute is ON for destination {env['FIVETRAN_GROUP_ID']} ✓")


def token_check():
    env = load_env()
    need(env, "FIVETRAN_GROUP_ID")
    if not env.get("LAKE_COMPUTE_TOKEN"):
        die("LAKE_COMPUTE_TOKEN is empty in .env. Generate a token on your destination's Lake Compute page, "
            "paste it into .env and save.")
    req = urllib.request.Request(TOKEN_EXCHANGE, method="POST",
                                 data=json.dumps({"credential": env["LAKE_COMPUTE_TOKEN"]}).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            jwt = json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        die(f"token rejected by Fivetran (HTTP {e.code}). Generate a new one on the destination page.")
    payload = jwt.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    group = claims.get("groupId") or claims.get("group_id")  # the live claim is groupId
    ttl = int(claims.get("exp", 0) - time.time())
    print(f"token accepted. destination={group}  access token lifetime ~{ttl // 60} min (renewed automatically)")
    if group != env["FIVETRAN_GROUP_ID"]:
        die(f"this token is for destination {group}, not {env['FIVETRAN_GROUP_ID']}. "
            "Generate one on the right destination's page.")
    print("token belongs to this destination ✓")


# ---- Step 4: Fivetran Platform connector -----------------------------------------------------------

def log_connector(sync=False):
    env = load_env()
    need(env, "FIVETRAN_GROUP_ID")
    items = fivetran(env, "GET", f"/groups/{env['FIVETRAN_GROUP_ID']}/connections?limit=1000") \
        .get("data", {}).get("items", [])
    logs = [c for c in items if c.get("service") == "fivetran_log"]
    if not logs:
        die("no Fivetran Platform connector in this destination. Add one in the dashboard "
            "(Connectors -> Add connector -> Fivetran Platform, destination = this one), then rerun.")
    c = max(logs, key=lambda x: x.get("succeeded_at") or "")  # reuse the one that synced most recently
    save_env({"LOG_SCHEMA": c["schema"], "LOG_CONNECTION_ID": c["id"]})
    print(f"Fivetran Platform connector {c['id']} schema={c['schema']} paused={c.get('paused')} "
          f"last_success={c.get('succeeded_at')}")
    if not sync:
        if not c.get("succeeded_at"):
            print("It hasn't finished a sync yet. Rerun with --sync to start one and wait for it.")
        return
    if c.get("paused"):
        fivetran(env, "PATCH", f"/connections/{c['id']}", {"paused": False})
    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    r = fivetran(env, "POST", f"/connections/{c['id']}/sync", {})
    print(f"sync: {r.get('code')} {r.get('message')}")
    for _ in range(90):  # up to ~30 minutes
        time.sleep(20)
        d = fivetran(env, "GET", f"/connections/{c['id']}").get("data", {})
        ok, bad = d.get("succeeded_at") or "", d.get("failed_at") or ""
        if ok[:19] > started:
            print(f"sync succeeded at {ok}")
            return
        if bad[:19] > started:
            die(f"sync failed at {bad}. Check the connector in the dashboard.")
        print(f"  ... {d.get('status', {}).get('sync_state')}")
    die("sync still running after 30 minutes; check the dashboard")


# ---- Step 5: build ---------------------------------------------------------------------------------

# Optional source tables per dbt package, and the package variable each one controls. Each connector's
# package has its own; add an entry when the quickstart supports another connector.
OPTIONAL_TABLES = {
    "fivetran_log": {  # LOG_SCHEMA
        "transformation_runs": "fivetran_platform_using_transformations",
        "credits_used": "fivetran_platform__credits_pricing",
        "usage_cost": "fivetran_platform__usage_pricing",
        "destination_membership": "fivetran_platform_using_destination_membership",
        "user": "fivetran_platform_using_user",
        "connection": "fivetran_platform_using_connection",
    },
}


def dbt_show(sql, marker):
    """Run one-row SQL on Lake Compute through ./bin/dbtw and return the text after `marker`."""
    out = subprocess.run([str(ROOT / "bin" / "dbtw"), "show", "--inline", sql, "--limit", "1"],
                         cwd=ROOT, capture_output=True, text=True, timeout=300)
    m = re.search(rf"{marker}([^\s│┆|]*)", out.stdout + out.stderr)
    return m.group(1) if m else None


def preflight(package="fivetran_log"):
    """List the connector's tables through Lake Compute and set each optional-table variable in
    dbt_project.yml to whether that table exists. Prints what it set and why."""
    env = load_env()
    need(env, "LC_CATALOG", "LOG_SCHEMA")
    sql = ("select 'TABLES:' || string_agg(lower(table_name), ',') as t from information_schema.tables "
           f"where table_catalog = '{env['LC_CATALOG']}' and table_schema = '{env['LOG_SCHEMA']}'")
    found = dbt_show(sql, "TABLES:")
    tables = set(filter(None, (found or "").split(",")))
    if not tables:
        die("couldn't list the connector's tables through Lake Compute; leaving dbt_project.yml unchanged. "
            "Run `./bin/dbtw parse` to check the project, then retry.")
    print(f"{len(tables)} tables in {env['LC_CATALOG']}.{env['LOG_SCHEMA']}")
    project = ROOT / "dbt_project.yml"
    text = project.read_text()
    for table, var in OPTIONAL_TABLES[package].items():
        value = "true" if table in tables else "false"
        text, n = re.subn(rf"^(\s*{var}:\s*)\S+", rf"\g<1>{value}", text, flags=re.M)
        if not n:
            die(f"{var} not found in dbt_project.yml")
        print(f"  {var}: {value}  ({table} {'present' if value == 'true' else 'absent'})")
    project.write_text(text)


def cost():
    """Lake Compute's own record of the last build: queries, bytes scanned and estimated cost
    (lakecompute_information_schema.jobs, estimated at $3 per TB scanned)."""
    load_env()
    rr = ROOT / "target" / "run_results.json"
    if not rr.exists():
        die("no target/run_results.json yet. Run the build first.")
    results = json.loads(rr.read_text())
    nodes = [r.get("unique_id", "") for r in results.get("results", [])]
    if not any(n.startswith("model.") for n in nodes):
        die("the last dbt command wasn't a build (run_results.json has no models). "
            "Run `python3 scripts/lc.py cost` right after `./bin/dbtw build`.")
    run_id = results.get("metadata", {}).get("invocation_id")
    elapsed = results.get("elapsed_time", 0)
    built = sum(1 for r in results["results"] if r.get("unique_id", "").startswith("model.")
                and r.get("status") == "success")
    sql = ("select 'COST:' || count(*) || ',' || count(bytes_scanned) || ',' || coalesce(sum(bytes_scanned), 0)"
           " || ',' || coalesce(sum(estimated_cost_usd), 0) as c from lakecompute_information_schema.jobs"
           f" where dbt_run_id = '{run_id}'")
    found = dbt_show(sql, "COST:")
    if not found:
        die("couldn't read lakecompute_information_schema.jobs. Run `./bin/dbtw debug` to check the connection.")
    queries, measured, scanned, usd = found.split(",")
    print(f"build {run_id}: {built} models in {int(elapsed) // 60}m {int(elapsed) % 60}s")
    print(f"queries on Lake Compute: {queries}  (bytes recorded for {measured})")
    print(f"data scanned: {int(float(scanned)) / 1e9:.3f} GB")
    print(f"estimated cost: ${float(usd):.6f}  (Lake Compute's estimate, $3 per TB scanned)")


# ---- Step 6: DuckDB --------------------------------------------------------------------------------

def install_duckdb():
    """DuckDB's command-line binary (DUCKDB_VERSION) into ./bin. Nothing else on the machine changes."""
    system, machine = platform.system(), platform.machine().lower()
    asset = {("Darwin", "arm64"): "osx-universal", ("Darwin", "x86_64"): "osx-universal",
             ("Linux", "x86_64"): "linux-amd64", ("Linux", "amd64"): "linux-amd64",
             ("Linux", "aarch64"): "linux-arm64", ("Linux", "arm64"): "linux-arm64"}.get((system, machine))
    if not asset:
        die(f"unsupported platform {system}/{machine}. Install DuckDB yourself: https://duckdb.org/install")
    name = f"duckdb_cli-{asset}.zip"
    url = f"https://github.com/duckdb/duckdb/releases/download/{DUCKDB_VERSION}/{name}"
    with urllib.request.urlopen(url, timeout=300) as r:
        zf = zipfile.ZipFile(io.BytesIO(r.read()))
    if "duckdb" not in zf.namelist():
        die(f"no duckdb binary in {name}")
    dest = install_binary(zf.read("duckdb"), "duckdb")
    print(f"installed DuckDB {DUCKDB_VERSION} to {dest}")


def duckdb_check():
    env = load_env()
    need(env, "LC_CATALOG", "POLARIS_ENDPOINT")
    if not env.get("POLARIS_CLIENT_ID") or not env.get("POLARIS_CLIENT_SECRET"):
        die("fill in POLARIS_CLIENT_ID and POLARIS_CLIENT_SECRET in .env, as a pair, with your destination's "
            "catalog client ID and secret.")
    sql = ("select table_schema, count(*) as tables from information_schema.tables "
           "where table_catalog = 'lake' and table_schema like 'quickstart%' group by 1 order by 1")
    out = subprocess.run([str(ROOT / "bin" / "lake"), "-c", sql], cwd=ROOT, capture_output=True, text=True,
                         timeout=300)
    text = redact(out.stdout + out.stderr)
    if out.returncode != 0:
        if "401" in text or "unauthorized" in text.lower():
            die("the catalog rejected POLARIS_CLIENT_ID and POLARIS_CLIENT_SECRET. They must be a matching pair "
                "for this destination's catalog.")
        die(f"DuckDB couldn't read the catalog:\n{text.strip()}")
    print(text.strip())
    if "quickstart" not in text:
        print("Connected, but no quickstart schemas yet. Run the build (step 5) first.")
    else:
        print("DuckDB reads your lake directly ✓  Open a SQL prompt with ./bin/lake")


COMMANDS = {
    "verify-key": lambda args: verify_key(),
    "destinations": lambda args: destinations(),
    "use-destination": lambda args: use_destination(args[0]) if args else die("usage: use-destination <group_id>"),
    "fusion-check": lambda args: fusion_check(),
    "install-fusion": lambda args: install_fusion(),
    "lake-compute": lambda args: lake_compute(),
    "token-check": lambda args: token_check(),
    "log-connector": lambda args: log_connector(sync="--sync" in args),
    "preflight": lambda args: preflight(),
    "cost": lambda args: cost(),
    "install-duckdb": lambda args: install_duckdb(),
    "duckdb-check": lambda args: duckdb_check(),
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    try:
        COMMANDS[sys.argv[1]](sys.argv[2:])
    except SystemExit:
        raise
    except Exception as e:  # never let a traceback carry a secret
        die(e)
