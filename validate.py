#!/usr/bin/env python3
"""Validate the BARQ assessment stack from the outside.

Every check is bounded, prints PASS or FAIL with the evidence it used, and the
script exits non-zero if anything failed. It is safe to re-run and it changes
nothing except the rows it creates through the public API.

It never targets a container by name alone: services are discovered through
`docker compose ps` for the requested project, so it cannot touch an unrelated
Compose project, and it adapts automatically when a third app instance is added.

    python validate.py
    python validate.py --url http://127.0.0.1:8090
    python validate.py --project barq-assessment --timeout 120
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_PROJECT = "barq-assessment"
EDGE_SERVICE = "nginx"
DATASTORES = ("postgres", "redis")
# Host ports the brief forbids this stack from publishing.
PROHIBITED_HOST_PORTS = (5432, 6379, 15432, 16379)

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if os.name == "nt" and not os.getenv("WT_SESSION") and not os.getenv("CI"):
    GREEN = RED = YELLOW = DIM = RESET = ""


class Report:
    """Collects results so a single failure cannot be lost in the scrollback."""

    def __init__(self):
        self.passed, self.failed, self.skipped = [], [], []

    def ok(self, name, detail=""):
        self.passed.append(name)
        print("{}PASS{} {:<38} {}{}{}".format(GREEN, RESET, name, DIM, detail, RESET))

    def fail(self, name, detail=""):
        self.failed.append((name, detail))
        print("{}FAIL{} {:<38} {}".format(RED, RESET, name, detail))

    def skip(self, name, detail=""):
        self.skipped.append(name)
        print("{}SKIP{} {:<38} {}{}{}".format(YELLOW, RESET, name, DIM, detail, RESET))

    def check(self, name, condition, detail=""):
        (self.ok if condition else self.fail)(name, detail)
        return bool(condition)


def run(args, timeout=30, check=True):
    """Run a command and return stdout, raising on failure when check is set."""
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError("{} failed: {}".format(" ".join(args), result.stderr.strip()))
    return result.stdout.strip()


def compose(project, *args, **kwargs):
    return run(["docker", "compose", "-p", project, *args], **kwargs)


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def http(url, method="GET", body=None, timeout=6.0, headers=None):
    """Return (status, headers, parsed-json-or-text). Never raises on 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw, status, head = response.read(), response.status, dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw, status, head = exc.read(), exc.code, dict(exc.headers)
    try:
        return status, head, json.loads(raw)
    except ValueError:
        return status, head, raw.decode(errors="replace")


def tcp_open(host, port, timeout=2.0):
    connection = socket.socket()
    connection.settimeout(timeout)
    try:
        connection.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        connection.close()


def wait_until(predicate, timeout, interval=2.0):
    """Bounded wait. Returns (ok, seconds_waited, last_value)."""
    deadline, started, value = time.monotonic() + timeout, time.monotonic(), None
    while True:
        ok, value = predicate()
        if ok:
            return True, time.monotonic() - started, value
        if time.monotonic() >= deadline:
            return False, time.monotonic() - started, value
        time.sleep(interval)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def discover(report, project):
    """Return (services_by_name, app_service_names) for the project."""
    try:
        raw = compose(project, "ps", "--format", "json", "-a")
    except Exception as exc:                                    # noqa: BLE001
        report.fail("compose-project-visible", str(exc))
        return None, None

    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("["):
            rows.extend(json.loads(line))
        elif line:
            rows.append(json.loads(line))

    if not rows:
        report.fail("compose-project-visible",
                    "no containers for project '{}' - is the stack up?".format(project))
        return None, None

    services = {row["Service"]: row for row in rows}
    apps = sorted(name for name in services if name.startswith("app-"))
    report.ok("compose-project-visible",
              "{} services: {}".format(len(services), ", ".join(sorted(services))))
    report.check("expected-services-present",
                 len(apps) >= 2 and EDGE_SERVICE in services
                 and all(store in services for store in DATASTORES),
                 "apps={} edge={} stores={}".format(
                     apps, EDGE_SERVICE in services,
                     [s for s in DATASTORES if s in services]))
    return services, apps


def check_container_health(report, services, timeout):
    """Every container must run, and every one with a probe must report healthy."""
    def probe():
        states = {}
        for service in services:
            try:
                state = inspect(services[service]["Name"])["State"]
            except Exception as exc:                            # noqa: BLE001
                states[service] = "inspect-error: {}".format(exc)
                continue
            health = state.get("Health", {}).get("Status")
            if state.get("Paused"):
                states[service] = "paused"
            else:
                states[service] = health or ("running" if state.get("Running") else "stopped")
        return all(value in ("healthy", "running") for value in states.values()), states

    ok, waited, states = wait_until(probe, timeout)
    report.check("containers-healthy", ok, "after {:.0f}s: {}".format(waited, states))
    return ok


def check_public_endpoints(report, base, timeout):
    ok, waited, value = wait_until(
        lambda: (lambda s, h, b: (s == 200, (s, b)))(*http(base + "/health")), timeout)
    if not report.check("public-http-reachable", ok,
                        "GET {}/health -> {} after {:.0f}s".format(base, value, waited)):
        return

    status, _, body = http(base + "/")
    report.check("endpoint-root",
                 status == 200 and "message" in body and "instance_id" in body,
                 "{} {}".format(status, body))

    status, _, body = http(base + "/health")
    report.check("endpoint-health", status == 200 and body.get("status") == "alive",
                 "{} {}".format(status, body))

    ok, waited, value = wait_until(
        lambda: (lambda s, h, b: (s == 200 and isinstance(b, dict)
                                  and b.get("status") == "ready", (s, b)))(
            *http(base + "/ready")), timeout)
    status, body = value
    report.check("endpoint-ready", ok, "{} {} after {:.0f}s".format(status, body, waited))
    if isinstance(body, dict):
        deps = body.get("dependencies", {})
        report.check("dependency-postgres-ready", deps.get("postgres") == "ready", str(deps))
        report.check("dependency-redis-ready", deps.get("redis") == "ready", str(deps))

    status, headers, body = http(base + "/instance")
    report.check("endpoint-instance",
                 status == 200 and headers.get("X-Instance-ID") == body.get("instance_id"),
                 "{} header={} body={}".format(status, headers.get("X-Instance-ID"),
                                               body.get("instance_id")))
    report.check("request-id-header", bool(headers.get("X-Request-ID")),
                 "X-Request-ID={}".format(headers.get("X-Request-ID")))

    status, _, body = http(base + "/definitely-not-a-route")
    report.check("unknown-route-404", status == 404, "{} {}".format(status, body))


def check_all_backends_serve(report, base, apps):
    """Every app instance must answer through the proxy, not just one of them."""
    attempts = max(24, len(apps) * 12)
    seen = {}
    for _ in range(attempts):
        status, _, body = http(base + "/instance")
        if status == 200 and isinstance(body, dict):
            key = body.get("instance_id")
            seen[key] = seen.get(key, 0) + 1
    missing = sorted(set(apps) - set(seen))
    unexpected = sorted(set(seen) - set(apps))
    report.check("all-backends-serve", not missing and not unexpected,
                 "{} requests -> {} (missing={} unexpected={})".format(
                     attempts, seen, missing or "none", unexpected or "none"))


def check_records_and_counter(report, base):
    marker = "validate.py {}".format(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    status, _, body = http(base + "/records", "POST", {"title": marker})
    created = body.get("record", {}) if isinstance(body, dict) else {}
    report.check("records-create-201", status == 201 and created.get("title") == marker,
                 "{} {}".format(status, created))

    status, _, body = http(base + "/records")
    titles = [row.get("title") for row in body.get("records", [])] if isinstance(body, dict) else []
    report.check("records-list-contains-new-row", status == 200 and marker in titles,
                 "{} rows listed, new row present={}".format(len(titles), marker in titles))

    bad_payloads = [{}, {"title": ""}, {"title": 5}, {"title": "x" * 201}]
    codes = [http(base + "/records", "POST", payload)[0] for payload in bad_payloads]
    report.check("records-reject-invalid-titles", all(code == 400 for code in codes),
                 "status codes for invalid payloads: {}".format(codes))

    first = http(base + "/counter")[2]
    second = http(base + "/counter")[2]
    report.check("counter-increments",
                 isinstance(first, dict) and isinstance(second, dict)
                 and second.get("counter") == first.get("counter", 0) + 1,
                 "{} -> {}".format(first.get("counter"), second.get("counter")))


def check_network_isolation(report, services, apps):
    """The edge proxy must have no route to the datastores; the apps must have one."""
    def networks(service):
        return set(inspect(services[service]["Name"])["NetworkSettings"]["Networks"])

    def suffixed(names, suffix):
        return sorted(name for name in names if name.endswith(suffix))

    edge = networks(EDGE_SERVICE)
    report.check("isolation-edge-frontend-only",
                 bool(suffixed(edge, "frontend")) and not suffixed(edge, "backend"),
                 "nginx networks: {}".format(sorted(edge)))

    for service in apps:
        nets = networks(service)
        report.check("isolation-app-both-networks[{}]".format(service),
                     bool(suffixed(nets, "frontend")) and bool(suffixed(nets, "backend")),
                     str(sorted(nets)))

    for store in DATASTORES:
        nets = networks(store)
        report.check("isolation-store-backend-only[{}]".format(store),
                     bool(suffixed(nets, "backend")) and not suffixed(nets, "frontend"),
                     str(sorted(nets)))

    # Prove the negative at runtime, not only from the declared topology.
    for store in DATASTORES:
        result = subprocess.run(
            ["docker", "exec", services[EDGE_SERVICE]["Name"], "sh", "-c",
             "getent hosts {} >/dev/null 2>&1; echo $?".format(store)],
            capture_output=True, text=True, timeout=20)
        report.check("isolation-edge-cannot-resolve[{}]".format(store),
                     result.stdout.strip() != "0",
                     "nginx getent {} exit={}".format(store, result.stdout.strip() or "?"))


def check_published_ports(report, services, public_port):
    """Only the edge may publish a host port, and forbidden ports must be closed."""
    published = {}
    for service in sorted(services):
        ports = inspect(services[service]["Name"])["NetworkSettings"]["Ports"] or {}
        bindings = sorted("{}:{}".format(bind.get("HostIp"), bind.get("HostPort"))
                          for binds in ports.values() if binds for bind in binds)
        if bindings:
            published[service] = bindings

    report.check("only-edge-publishes-a-host-port", set(published) <= {EDGE_SERVICE},
                 "published: {}".format(published or "none"))
    report.check("edge-publishes-expected-port",
                 any(str(public_port) in binding
                     for binding in published.get(EDGE_SERVICE, [])),
                 "expected {}, found {}".format(public_port, published.get(EDGE_SERVICE)))

    for port in PROHIBITED_HOST_PORTS:
        report.check("prohibited-host-port-closed[{}]".format(port),
                     not tcp_open("127.0.0.1", port),
                     "127.0.0.1:{} must not accept connections".format(port))


def check_runtime_hardening(report, services, apps):
    for service in list(apps) + [EDGE_SERVICE]:
        uid = run(["docker", "exec", services[service]["Name"], "id", "-u"],
                  check=False).strip()
        report.check("runs-as-non-root[{}]".format(service), uid not in ("", "0"),
                     "uid={}".format(uid or "unknown"))

    for service in sorted(services):
        host_config = inspect(services[service]["Name"])["HostConfig"]
        policy = (host_config.get("RestartPolicy") or {}).get("Name")
        report.check("restart-policy-set[{}]".format(service),
                     policy not in (None, "", "no"), "restart={}".format(policy))
        report.check("memory-limit-set[{}]".format(service),
                     (host_config.get("Memory") or 0) > 0,
                     "{} bytes".format(host_config.get("Memory") or 0))


def check_persistence_config(report, services):
    """PGDATA must live on a named volume, and Redis must be configured to persist."""
    pgdata = "/var/lib/postgresql/data"
    postgres = inspect(services["postgres"]["Name"])
    volumes = [m for m in postgres.get("Mounts", []) if m["Type"] == "volume"]
    on_volume = any(pgdata == m["Destination"]
                    or pgdata.startswith(m["Destination"].rstrip("/") + "/")
                    for m in volumes)
    report.check("postgres-pgdata-on-named-volume", on_volume,
                 str([(m.get("Name"), m["Destination"]) for m in volumes]) or "no volumes")

    tmpfs = postgres["HostConfig"].get("Tmpfs") or {}
    report.check("postgres-pgdata-not-tmpfs",
                 not any(path.startswith(pgdata) for path in tmpfs),
                 "tmpfs: {}".format(tmpfs or "none"))

    appendonly = run(["docker", "exec", services["redis"]["Name"],
                      "redis-cli", "config", "get", "appendonly"], check=False)
    report.check("redis-persistence-enabled", "yes" in appendonly.lower(),
                 appendonly.replace("\n", " ") or "redis-cli config get failed")


def resolve_public_port(explicit_url):
    if explicit_url:
        tail = explicit_url.rstrip("/").rsplit(":", 1)[-1]
        return tail if tail.isdigit() else "80"
    port = os.getenv("PUBLIC_PORT")
    if not port and os.path.exists(".env"):
        with open(".env", encoding="utf-8") as handle:
            for line in handle:
                if line.strip().startswith("PUBLIC_PORT="):
                    port = line.strip().split("=", 1)[1].strip()
    return port or "8080"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project",
                        default=os.getenv("COMPOSE_PROJECT_NAME", DEFAULT_PROJECT))
    parser.add_argument("--url", default=None,
                        help="public base URL; defaults to PUBLIC_PORT from .env on loopback")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="bounded wait, in seconds, for readiness (default 90)")
    args = parser.parse_args()

    public_port = resolve_public_port(args.url)
    base = (args.url or "http://127.0.0.1:{}".format(public_port)).rstrip("/")

    print("project={}  url={}  bounded-wait={}s".format(args.project, base, args.timeout))
    print("-" * 78)
    started = time.monotonic()
    report = Report()

    services, apps = discover(report, args.project)
    if services:
        if check_container_health(report, services, args.timeout):
            check_public_endpoints(report, base, args.timeout)
            check_all_backends_serve(report, base, apps)
            check_records_and_counter(report, base)
        else:
            report.skip("public-endpoint-checks", "containers never became healthy")
        check_network_isolation(report, services, apps)
        check_published_ports(report, services, public_port)
        check_runtime_hardening(report, services, apps)
        check_persistence_config(report, services)

    print("-" * 78)
    print("{} passed, {} failed, {} skipped in {:.1f}s".format(
        len(report.passed), len(report.failed), len(report.skipped),
        time.monotonic() - started))
    for name, detail in report.failed:
        print("  {}FAILED{} {}: {}".format(RED, RESET, name, detail))
    return 1 if report.failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
