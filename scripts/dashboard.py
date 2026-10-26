#!/usr/bin/env python3
"""Local progress page for the quickstart. Start it with: python3 scripts/lc.py dashboard

Runs the same checks as scripts/lc.py, one per step, and the same commands (install, sync, build). Listens on
127.0.0.1 only, and every request needs the one-time token in the URL it opens. Secrets stay in .env: the page
gets check results and non-secret settings, never .env values.
"""
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lc  # noqa: E402

ROOT = lc.ROOT
LC = [sys.executable, str(ROOT / "scripts" / "lc.py")]
DBTW = str(ROOT / "bin" / "dbtw")
PAGE = ROOT / "scripts" / "dashboard.html"
BUILD = ROOT / "target" / "quickstart_build.json"
RESULTS = ROOT / "target" / "quickstart_results.json"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
GROUP_ID = re.compile(r"^[A-Za-z0-9_]+$")

# Each step's check: lc.py commands that must all pass. "build" is checked from target/ instead.
CHECKS = {
    "prereqs": [["verify-key"]],
    "fusion": [["fusion-check"]],
    "lake_compute": [["lake-compute"]],
    "token": [["token-check"]],
    "log": [["log-connector"]],
    "duckdb": [["duckdb-check"]],
}
ACTIONS = {
    "install-fusion": [LC + ["install-fusion"]],
    "sync-log": [LC + ["log-connector", "--sync"]],
    "build": [[DBTW, "deps"], [DBTW, "parse"], LC + ["preflight"],
              [DBTW, "build", "--selector", "fivetran_log"], LC + ["cost"]],
    "install-duckdb": [LC + ["install-duckdb"]],
}
# Settings the page may show. Everything else in .env stays on this machine.
SHOWN = ["FIVETRAN_GROUP_ID", "LC_CATALOG", "MDLS_REGION", "MDLS_BUCKET", "LOG_SCHEMA", "POLARIS_ENDPOINT"]


def env():
    if not lc.ENV.exists():
        return {}
    lc._ENV_CACHE.clear()
    return lc.load_env()


def clean(text):
    return lc.redact(ANSI.sub("", text))


def run(cmd, timeout=600):
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    return out.returncode, clean(out.stdout + out.stderr)


def lines_of(text):
    """Turn check output into rows for the page: ok, no or info."""
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith(("┌", "│", "├", "└", "╞")) or re.match(r"^\d+ rows?\b", line):
            continue
        if line.startswith("ERROR:"):
            rows.append({"kind": "no", "text": line[6:].strip()})
        elif "✓" in line:
            rows.append({"kind": "ok", "text": line.replace("✓", "").strip()})
        else:
            rows.append({"kind": "info", "text": line})
    return rows


def check(step):
    if not lc.ENV.exists():
        return {"state": "need", "missing_env": True,
                "rows": [{"kind": "no", "text": ".env doesn't exist yet. Click Create .env, then fill in your API key."}]}
    if step == "build":
        return check_build()
    rows, ok = [], True
    for args in CHECKS[step]:
        rc, text = run(LC + args)
        rows += lines_of(text)
        ok = ok and rc == 0
        if not ok:
            break
    result = {"state": "done" if ok else "need", "rows": rows}
    if step == "prereqs" and ok and not env().get("FIVETRAN_GROUP_ID"):
        rc, text = run(LC + ["destinations"])
        result.update(state="need", choices=parse_destinations(text))
        rows.append({"kind": "no", "text": "Pick the Managed Data Lake destination to use."})
    elif step == "prereqs" and ok:
        e = env()
        rows.append({"kind": "ok", "text": f"Destination {e['FIVETRAN_GROUP_ID']} (catalog {e.get('LC_CATALOG')})"})
    if step == "duckdb" and not ok and "DuckDB not found" in json.dumps(rows):
        result["offer"] = "install-duckdb"
    if step == "log" and ok and "hasn't finished a sync" in json.dumps(rows):
        result.update(state="need", offer="sync-log")
    return result


def parse_destinations(text):
    out = []
    for line in text.splitlines():
        m = re.match(r"^(\S+)\s+name='?(.*?)'?\s+region=(\S*)\s+lake_compute=(\S+)\s+catalog=(\S*)", line)
        if m:
            out.append(dict(zip(["id", "name", "region", "lake_compute", "catalog"], m.groups())))
    return out


def check_build():
    if not BUILD.exists():
        return {"state": "ready", "rows": [{"kind": "info", "text": "Not built yet."}]}
    b = json.loads(BUILD.read_text())
    ok = b["models_failed"] == 0 and b["tests_failed"] == 0
    rows = [{"kind": "ok" if ok else "no",
             "text": f"{b['models_ok']} models and {b['tests_passed']} tests passed"
                     + (f", {b['models_failed']} models and {b['tests_failed']} tests failed" if not ok else ""),
             "detail": f"{int(b['elapsed']) // 60}m {int(b['elapsed']) % 60}s on Lake Compute"},
            {"kind": "ok", "text": f"Estimated cost ${b['usd']:.6f}",
             "detail": f"about ${b['warehouse_usd']:.2f} on a typical cloud data warehouse"}]
    return {"state": "done" if ok else "need", "rows": rows}


# ---- background jobs (install, sync, build) --------------------------------------------------------

JOBS, JOB_LOCK = {}, threading.Lock()


def start_job(name, cmds):
    with JOB_LOCK:
        if any(not j["done"] for j in JOBS.values()):
            return None
        job_id = secrets.token_hex(4)
        JOBS[job_id] = {"name": name, "lines": [], "done": False, "rc": None}
    threading.Thread(target=run_job, args=(job_id, cmds), daemon=True).start()
    return job_id


def run_job(job_id, cmds):
    job = JOBS[job_id]
    rc = 0
    for cmd in cmds:
        shown = " ".join(os.path.relpath(c, ROOT) if c.startswith(str(ROOT)) else os.path.basename(c)
                         if c == sys.executable else c for c in cmd)
        job["lines"].append(f"$ {shown}")
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            job["lines"].append(clean(line.rstrip("\n")))
        rc = p.wait()
        if rc != 0:
            job["lines"].append(f"(stopped: exit code {rc})")
            break
    if job["name"] == "build":
        RESULTS.unlink(missing_ok=True)
    job.update(done=True, rc=rc)


# ---- results ---------------------------------------------------------------------------------------

def query(sql, limit=10):
    """Rows for one SELECT: straight from the lake with DuckDB if it's set up, otherwise through Lake Compute."""
    e = env()
    if e.get("POLARIS_CLIENT_SECRET") and (shutil.which("duckdb") or (ROOT / "bin" / "duckdb").exists()):
        rc, text = run([str(ROOT / "bin" / "lake"), "-json", "-c", sql.format(db="lake")], timeout=300)
        if rc == 0:
            return parse_json_rows(text), "DuckDB, straight from your lake"
    rc, text = run([DBTW, "show", "--inline", sql.format(db=e.get("LC_CATALOG", "")), "--output", "json",
                    "--limit", str(limit)], timeout=300)
    return parse_json_rows(text), "Lake Compute"


def parse_json_rows(text):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    m = re.search(r"\[\s*\{.*\}\s*\]", text, re.S)  # DuckDB's -json output spans several lines
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    return []


def results():
    if not BUILD.exists():
        return {"ready": False}
    build = json.loads(BUILD.read_text())
    if RESULTS.exists():
        cached = json.loads(RESULTS.read_text())
        if cached.get("run_id") == build["run_id"]:
            return cached
    manifest = json.loads((ROOT / "target" / "manifest.json").read_text())
    models = [n for n in manifest["nodes"].values()
              if n.get("package_name") == "fivetran_log" and n["resource_type"] == "model"]
    sources = sorted(s["identifier"] for s in manifest["sources"].values() if s.get("package_name") == "fivetran_log")
    staging = sorted(n["name"] for n in models if n["name"].startswith("stg_"))
    final = sorted(({"name": n["name"].replace("fivetran_platform__", ""), "schema": n["schema"],
                     "description": first_sentence(n.get("description", "")),
                     "columns": len(n.get("columns", {}))}
                    for n in models if not n["name"].startswith("stg_")), key=lambda x: x["name"])
    status = next(n for n in models if n["name"] == "fivetran_platform__connection_status")
    e = env()
    log_table = "{db}." + f"{e['LOG_SCHEMA']}.log"
    status_table = "{db}." + f"{status['schema']}.{status['alias']}"
    raw, engine = query(f"select time_stamp, message_event, message_data from {log_table} "
                        "where message_data is not null order by time_stamp desc limit 4", 4)
    after, _ = query(f"select connection_name, connector_type, connection_health, last_successful_sync_completed_at, "
                     f"number_errors_since_last_completed_sync as errors from {status_table} "
                     "order by case connection_health when 'broken' then 0 when 'delayed' then 1 else 2 end, "
                     "connection_name limit 6", 6)
    counts, _ = query(f"select (select count(*) from {log_table}) as log_rows, (select count(*) from {status_table}) "
                      "as status_rows", 1)
    data = {"ready": True, "run_id": build["run_id"], "build": build, "engine": engine,
            "raw": raw, "after": after, "counts": counts[0] if counts else {},
            "log_table": f"{e['LOG_SCHEMA']}.log", "status_table": f"{status['schema']}.{status['alias']}",
            "sources": sources, "staging": staging, "final": final,
            "staging_schema": next((n["schema"] for n in models if n["name"].startswith("stg_")), ""),
            "final_schema": status["schema"]}
    RESULTS.write_text(json.dumps(data, default=str))
    return data


def first_sentence(text):
    text = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return m.group(1) if m else text


# ---- HTTP ------------------------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    token = ""
    port = 0

    def log_message(self, *args):
        pass

    def allowed(self):
        host_ok = self.headers.get("Host") in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")
        return host_ok and secrets.compare_digest(self.headers.get("X-Quickstart-Token", ""), self.token)

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            q = urllib.parse.parse_qs(url.query)
            if not secrets.compare_digest(q.get("t", [""])[0], self.token):
                return self.send(403, b"Open the link printed by `python3 scripts/lc.py dashboard`.", "text/plain")
            html = PAGE.read_text().replace("__QUICKSTART_TOKEN__", self.token)
            return self.send(200, html.encode(), "text/html; charset=utf-8")
        if not self.allowed():
            return self.send(403, {"error": "forbidden"})
        if url.path == "/api/env":
            e = env()
            return self.send(200, {"has_env": lc.ENV.exists(), **{k: e.get(k, "") for k in SHOWN}})
        if url.path.startswith("/api/job/"):
            job = JOBS.get(url.path.rsplit("/", 1)[1])
            if not job:
                return self.send(404, {"error": "no such job"})
            since = int(urllib.parse.parse_qs(url.query).get("since", ["0"])[0])
            return self.send(200, {"name": job["name"], "lines": job["lines"][since:], "next": len(job["lines"]),
                                   "done": job["done"], "rc": job["rc"]})
        if url.path == "/api/results":
            try:
                return self.send(200, results())
            except Exception as ex:  # keep secrets out of tracebacks
                return self.send(500, {"error": clean(str(ex))})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed():
            return self.send(403, {"error": "forbidden"})
        url = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        parts = url.path.strip("/").split("/")
        if parts[:2] == ["api", "check"] and len(parts) == 3 and (parts[2] in CHECKS or parts[2] == "build"):
            try:
                return self.send(200, check(parts[2]))
            except Exception as ex:
                return self.send(200, {"state": "need", "rows": [{"kind": "no", "text": clean(str(ex))}]})
        if url.path == "/api/create-env":
            if not lc.ENV.exists():
                shutil.copy(ROOT / ".env.example", lc.ENV)
                os.chmod(lc.ENV, 0o600)
            return self.send(200, {"ok": True})
        if url.path == "/api/use-destination":
            gid = str(body.get("id", ""))
            if not GROUP_ID.match(gid):
                return self.send(400, {"error": "bad destination id"})
            rc, text = run(LC + ["use-destination", gid])
            return self.send(200, {"ok": rc == 0, "rows": lines_of(text)})
        if parts[:2] == ["api", "action"] and len(parts) == 3 and parts[2] in ACTIONS:
            job_id = start_job(parts[2], ACTIONS[parts[2]])
            if not job_id:
                return self.send(409, {"error": "Another task is still running."})
            return self.send(200, {"job": job_id})
        self.send(404, {"error": "not found"})


def main(port=0):
    Handler.token = secrets.token_urlsafe(18)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    Handler.port = server.server_address[1]
    url = f"http://127.0.0.1:{Handler.port}/?t={Handler.token}"
    print(f"Quickstart progress page: {url}")
    print("Leave this running while you work through the steps. Press Ctrl+C to stop it.")
    if not os.environ.get("QUICKSTART_NO_BROWSER"):
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
