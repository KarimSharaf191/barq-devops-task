#!/usr/bin/env python3
"""Backend failure and recovery test for the BARQ assessment stack.

Drives continuous traffic through NGINX in three phases - baseline, one backend
stopped, backend restored - and reports requests, errors, availability, latency
and which instance served what in each phase.

Scope and safety:
  * the victim is resolved through `docker compose ps` for the requested project,
    so an unrelated container can never be stopped;
  * the victim is restarted in a finally block, so Ctrl+C or an assertion failure
    still leaves the stack running;
  * the run exits non-zero if GET availability during the outage drops below the
    threshold, or if the recovered instance does not serve traffic again.

    python failure_test.py
    python failure_test.py --victim app-02 --seconds 12 --url http://127.0.0.1:8090
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

DEFAULT_PROJECT = "barq-assessment"


def run(args, timeout=60, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError("{} failed: {}".format(" ".join(args), result.stderr.strip()))
    return result.stdout.strip()


def compose_services(project):
    raw = run(["docker", "compose", "-p", project, "ps", "--format", "json", "-a"])
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("["):
            rows.extend(json.loads(line))
        elif line:
            rows.append(json.loads(line))
    return {row["Service"]: row["Name"] for row in rows}


def container_health(name):
    raw = run(["docker", "inspect", name], check=False)
    if not raw:
        return "absent"
    state = json.loads(raw)[0]["State"]
    if state.get("Paused"):
        return "paused"
    return state.get("Health", {}).get("Status") or (
        "running" if state.get("Running") else "stopped")


def wait_for_health(name, wanted, timeout):
    deadline = time.monotonic() + timeout
    while True:
        current = container_health(name)
        if current == wanted:
            return True, current
        if time.monotonic() >= deadline:
            return False, current
        time.sleep(1.0)


class Phase:
    """One measurement window: counts, errors and latencies per instance."""

    def __init__(self, label):
        self.label = label
        self.lock = threading.Lock()
        self.get_ok = self.get_err = 0
        self.post_ok = self.post_err = 0
        self.by_instance = {}
        self.statuses = {}
        self.latencies = []

    def record(self, method, status, instance, seconds, error=None):
        with self.lock:
            key = error or str(status)
            self.statuses[key] = self.statuses.get(key, 0) + 1
            good = status is not None and 200 <= status < 400
            if method == "GET":
                self.get_ok, self.get_err = (self.get_ok + 1, self.get_err) if good \
                    else (self.get_ok, self.get_err + 1)
            else:
                self.post_ok, self.post_err = (self.post_ok + 1, self.post_err) if good \
                    else (self.post_ok, self.post_err + 1)
            if instance:
                self.by_instance[instance] = self.by_instance.get(instance, 0) + 1
            if good:
                self.latencies.append(seconds * 1000.0)

    @property
    def get_total(self):
        return self.get_ok + self.get_err

    @property
    def availability(self):
        return 100.0 * self.get_ok / self.get_total if self.get_total else 0.0

    def percentile(self, fraction):
        """Nearest-rank percentile on the sorted sample, in milliseconds."""
        if not self.latencies:
            return float("nan")
        ordered = sorted(self.latencies)
        rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
        return ordered[rank - 1]

    def summary(self):
        median = statistics.median(self.latencies) if self.latencies else float("nan")
        return (
            "{:<22} GET {:>4}/{:<4} ok ({:6.2f}% available)  POST {:>2}/{:<2} ok  "
            "p50 {:6.1f}ms  p95 {:6.1f}ms\n{:<22} served by {}  statuses {}".format(
                self.label, self.get_ok, self.get_total, self.availability,
                self.post_ok, self.post_ok + self.post_err, median, self.percentile(0.95),
                "", self.by_instance or "-", self.statuses))


def request(url, method="GET", body=None, timeout=8.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            response.read()
            return response.status, response.headers.get("X-Instance-ID"), \
                time.monotonic() - started, None
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, exc.headers.get("X-Instance-ID"), time.monotonic() - started, None
    except Exception as exc:                                    # noqa: BLE001
        return None, None, time.monotonic() - started, type(exc).__name__


def drive(base, phase, seconds, workers=4, post_every=8):
    """Send traffic until the phase window closes. Mostly GET, some POST."""
    deadline = time.monotonic() + seconds
    counter = {"n": 0}
    counter_lock = threading.Lock()

    def worker():
        while time.monotonic() < deadline:
            with counter_lock:
                counter["n"] += 1
                index = counter["n"]
            if index % post_every == 0:
                status, instance, elapsed, error = request(
                    base + "/records", "POST",
                    {"title": "failure_test {} #{}".format(phase.label, index)})
                phase.record("POST", status, instance, elapsed, error)
            else:
                status, instance, elapsed, error = request(base + "/instance")
                phase.record("GET", status, instance, elapsed, error)
            time.sleep(0.05)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=seconds + 20)
    return phase


def resolve_base(url):
    if url:
        return url.rstrip("/")
    port = os.getenv("PUBLIC_PORT")
    if not port and os.path.exists(".env"):
        with open(".env", encoding="utf-8") as handle:
            for line in handle:
                if line.strip().startswith("PUBLIC_PORT="):
                    port = line.strip().split("=", 1)[1].strip()
    return "http://127.0.0.1:{}".format(port or "8080")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=os.getenv("COMPOSE_PROJECT_NAME", DEFAULT_PROJECT))
    parser.add_argument("--url", default=None)
    parser.add_argument("--victim", default="app-01", help="app service to stop")
    parser.add_argument("--seconds", type=float, default=10.0, help="length of each phase")
    parser.add_argument("--recovery-timeout", type=float, default=90.0)
    parser.add_argument("--min-availability", type=float, default=99.0,
                        help="minimum GET availability during the outage, percent")
    args = parser.parse_args()

    base = resolve_base(args.url)
    services = compose_services(args.project)
    apps = sorted(name for name in services if name.startswith("app-"))

    if args.victim not in services:
        print("victim '{}' is not a service of project '{}' (services: {})".format(
            args.victim, args.project, sorted(services)), file=sys.stderr)
        return 2
    if len(apps) < 2:
        print("need at least two app instances to test failover, found {}".format(apps),
              file=sys.stderr)
        return 2

    victim = services[args.victim]
    survivors = [name for name in apps if name != args.victim]
    print("project={}  url={}  victim={} ({})  survivors={}  phase={}s".format(
        args.project, base, args.victim, victim, survivors, args.seconds))
    print("=" * 78)

    baseline = Phase("1 baseline")
    outage = Phase("2 {} stopped".format(args.victim))
    recovered = Phase("3 recovered")
    stopped = False
    try:
        drive(base, baseline, args.seconds)
        print(baseline.summary())

        print("\n--> docker stop {}".format(victim))
        run(["docker", "stop", victim])
        stopped = True
        print("    health now: {}".format(container_health(victim)))

        drive(base, outage, args.seconds)
        print(outage.summary())
    finally:
        if stopped:
            print("\n--> docker start {}  (always runs, even on error or Ctrl+C)".format(victim))
            run(["docker", "start", victim], check=False)
            ok, state = wait_for_health(victim, "healthy", args.recovery_timeout)
            print("    health now: {} (healthy within timeout: {})".format(state, ok))

    drive(base, recovered, args.seconds)
    print(recovered.summary())

    print("=" * 78)
    failures = []

    if outage.availability < args.min_availability:
        failures.append("availability during the outage was {:.2f}%, below {:.2f}%".format(
            outage.availability, args.min_availability))
    if set(outage.by_instance) & {args.victim}:
        failures.append("the stopped instance {} still served traffic".format(args.victim))
    if not set(outage.by_instance) & set(survivors):
        failures.append("no surviving instance served traffic during the outage")
    if args.victim not in recovered.by_instance:
        failures.append("{} did not serve traffic after recovery".format(args.victim))
    if recovered.availability < args.min_availability:
        failures.append("availability after recovery was {:.2f}%".format(recovered.availability))

    print("GET availability   baseline {:.2f}%   outage {:.2f}%   recovered {:.2f}%".format(
        baseline.availability, outage.availability, recovered.availability))
    print("POST errors        baseline {}   outage {}   recovered {}".format(
        baseline.post_err, outage.post_err, recovered.post_err))
    if outage.post_err:
        print("  note: POST failures during the outage are expected and deliberate -"
              "\n        proxy_next_upstream does not include non_idempotent, so a write is"
              "\n        never retried into a possible duplicate insert. See decisions.md.")

    for line in failures:
        print("FAIL: {}".format(line))
    print("RESULT: {}".format("FAIL" if failures else "PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
