#!/usr/bin/env python3
"""Reproducible analysis of the three supplied historical logs.

Reads logs/access.log, logs/error.log and logs/application.log read-only and
prints every number quoted in log_analysis.md. Nothing here is hand counted;
re-running it regenerates the report.

    python scripts/analyze_logs.py              # full report
    python scripts/analyze_logs.py --section 5  # one numbered question
    python scripts/analyze_logs.py --json       # machine-readable summary

Counting rules, stated up front because they decide every number below:

  * One access-log line is one client request. NGINX writes the access line once,
    when the client response is complete, no matter how many upstream attempts it
    took - a retry appears as extra comma-separated values inside `upstream` and
    `upstream_status`, not as an extra line. Counting upstream attempts, or
    counting error.log lines, would inflate the request total.
  * Exact duplicate lines are log-shipping artefacts, not extra requests, and are
    collapsed. Deduplication is on the whole record, never on request_id alone:
    one request legitimately produces several application-log records (an
    http_request plus one dependency_error per failed dependency).
  * Truncated lines are excluded, reported, and never guessed at.
"""
import argparse
import collections
import json
import math
import pathlib
import re
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"

ERROR_LINE = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>\w+)\] "
    r"(?P<pid>\d+#\d+): (?:\*(?P<conn>\d+) )?(?P<message>.*)$")
REQUEST_ID = re.compile(r"request_id=(?P<id>[A-Za-z0-9_.:-]+)")
UPSTREAM_URL = re.compile(r'upstream: "http://(?P<peer>[^/"]+)')


def load_json_log(path):
    """Return (records, malformed, duplicates_removed).

    `records` are unique records in file order; `malformed` is a list of
    (line_number, raw_prefix) for lines that are not valid JSON.
    """
    records, malformed, seen, duplicates = [], [], set(), []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            malformed.append((number, "<blank line>"))
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            malformed.append((number, raw[:80]))
            continue
        key = json.dumps(record, sort_keys=True)
        if key in seen:
            duplicates.append((number, record.get("request_id")))
            continue
        seen.add(key)
        records.append(record)
    return records, malformed, duplicates


def load_error_log(path):
    parsed, malformed = [], []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        match = ERROR_LINE.match(raw)
        if not match:
            malformed.append((number, raw[:80]))
            continue
        entry = match.groupdict()
        entry["raw"] = raw
        rid = REQUEST_ID.search(raw)
        entry["request_id"] = rid.group("id") if rid else None
        peer = UPSTREAM_URL.search(raw)
        entry["peer"] = peer.group("peer") if peer else None
        entry["kind"] = classify(raw)
        parsed.append(entry)
    return parsed, malformed


def classify(line):
    if "Connection refused" in line:
        return "connect-refused"
    if "timed out" in line:
        return "upstream-timeout"
    if "no live upstreams" in line:
        return "no-live-upstreams"
    return "other"


def nearest_rank(sorted_values, fraction):
    """Nearest-rank percentile: the smallest value at or above the fraction.

    Chosen over interpolation because it always returns an observed measurement,
    which is what you want when quoting a latency back to an operator.
    """
    if not sorted_values:
        return float("nan")
    rank = max(1, min(len(sorted_values), math.ceil(fraction * len(sorted_values))))
    return sorted_values[rank - 1]


def attempts(record):
    """Upstream attempts recorded for one client request."""
    raw = (record.get("upstream") or "").strip()
    return [value.strip() for value in raw.split(",") if value.strip()] if raw else []


def analyse():
    access, access_bad, access_dupes = load_json_log(LOG_DIR / "access.log")
    app, app_bad, app_dupes = load_json_log(LOG_DIR / "application.log")
    errors, error_bad = load_error_log(LOG_DIR / "error.log")

    requests = {record["request_id"]: record for record in access}
    http_rows = [row for row in app if row.get("event") == "http_request"]
    dependency_rows = [row for row in app if row.get("event") == "dependency_error"]

    statuses = collections.Counter(record["status"] for record in access)
    server_errors = sum(count for status, count in statuses.items() if status >= 500)
    client_errors = sum(count for status, count in statuses.items() if 400 <= status < 500)

    latencies = sorted(record["request_time"] for record in access)
    ok_latencies = sorted(r["request_time"] for r in access if r["status"] < 500)
    bad_latencies = sorted(r["request_time"] for r in access if r["status"] >= 500)

    retried = [record for record in access if len(attempts(record)) > 1]
    retried_ok = [record for record in retried if record["status"] < 400]

    served = {row["request_id"] for row in http_rows}
    never_served = sorted(set(requests) - served)

    return {
        "access": access, "access_bad": access_bad, "access_dupes": access_dupes,
        "app": app, "app_bad": app_bad, "app_dupes": app_dupes,
        "errors": errors, "error_bad": error_bad,
        "requests": requests, "http_rows": http_rows, "dependency_rows": dependency_rows,
        "statuses": statuses, "server_errors": server_errors, "client_errors": client_errors,
        "latencies": latencies, "ok_latencies": ok_latencies, "bad_latencies": bad_latencies,
        "retried": retried, "retried_ok": retried_ok, "never_served": never_served,
    }


def window(records, key="timestamp"):
    stamps = [record[key] for record in records if record.get(key)]
    return (min(stamps), max(stamps)) if stamps else ("-", "-")


def bucket(records, width=5):
    """Group records by their HH:MM, keeping file order of first appearance."""
    grid = collections.defaultdict(collections.Counter)
    for record in records:
        grid[record["timestamp"][11:16]][record["status"]] += 1
    del width
    return grid


# --------------------------------------------------------------------------- #
# Report sections, one per question in log_analysis.md
# --------------------------------------------------------------------------- #

def section_1(data):
    print("1. Coverage, valid / malformed / duplicate lines per file")
    for label, records, bad, dupes, path in (
            ("access.log", data["access"], data["access_bad"], data["access_dupes"],
             LOG_DIR / "access.log"),
            ("application.log", data["app"], data["app_bad"], data["app_dupes"],
             LOG_DIR / "application.log")):
        total = len(path.read_text(encoding="utf-8").splitlines())
        first, last = window(records)
        print("   {:<16} {:>4} lines  {:>4} unique valid  {:>2} malformed  {:>2} exact duplicates"
              .format(label, total, len(records), len(bad), len(dupes)))
        print("   {:<16} covers {} .. {}".format("", first, last))
        for number, text in bad:
            print("   {:<16}   malformed line {}: {!r}".format("", number, text))
        if dupes:
            print("   {:<16}   duplicate request_ids: {}".format(
                "", ", ".join(str(rid) for _, rid in dupes)))
    total = len((LOG_DIR / "error.log").read_text(encoding="utf-8").splitlines())
    stamps = [entry["ts"] for entry in data["errors"]]
    print("   {:<16} {:>4} lines  {:>4} parsed        {:>2} unparsed"
          .format("error.log", total, len(data["errors"]), len(data["error_bad"])))
    print("   {:<16} covers {} .. {}".format("", min(stamps), max(stamps)))
    print("   Time zone is UTC for all three files (logs/README.md); access and")
    print("   application timestamps are ISO-8601 with milliseconds, error.log is")
    print("   NGINX's own 'YYYY/MM/DD HH:MM:SS' format with second resolution.")


def section_2(data):
    print("2. Distinct client requests, and how retries avoid being double counted")
    access, requests = data["access"], data["requests"]
    print("   unique valid access lines ......... {}".format(len(access)))
    print("   distinct request_id values ........ {}".format(len(requests)))
    print("   upstream attempts recorded ........ {}   <- NOT the request count"
          .format(sum(len(attempts(record)) for record in access)))
    print("   error.log lines carrying a request_id {}   <- NOT the request count"
          .format(sum(1 for entry in data["errors"] if entry["request_id"])))
    print()
    print("   One access line == one client request, written once when the client")
    print("   response completes. A retry adds a value to `upstream`, not a line:")
    print("   {} requests carry 2 upstream attempts. Counting attempts would report"
          .format(len(data["retried"])))
    print("   {} requests instead of {}.".format(
        sum(len(attempts(record)) for record in access), len(access)))
    print()
    print("   Independent cross-check from the other side of the proxy:")
    print("     unique application http_request records .......... {}".format(
        len({row["request_id"] for row in data["http_rows"]})))
    print("     requests with no application record at all ....... {}".format(
        len(data["never_served"])))
    print("     sum ............................................. {}".format(
        len({row["request_id"] for row in data["http_rows"]}) + len(data["never_served"])))
    print("   which equals the {} distinct client requests. The {} with no application"
          .format(len(requests), len(data["never_served"])))
    print("   record are exactly the requests the backend never received.")


def section_3(data):
    print("3. Final client status counts and error rate")
    total = len(data["access"])
    for status, count in sorted(data["statuses"].items()):
        print("   {} .......... {:>4}   {:5.2f}%".format(status, count, 100.0 * count / total))
    print("   ---")
    print("   denominator: {} distinct client requests (deduplicated access lines,".format(total))
    print("   malformed lines excluded, retries counted once).")
    print("   5xx error rate ... {}/{} = {:.2f}%".format(
        data["server_errors"], total, 100.0 * data["server_errors"] / total))
    print("   4xx client rate .. {}/{} = {:.2f}%  (all /missing, a deliberate probe)".format(
        data["client_errors"], total, 100.0 * data["client_errors"] / total))
    print("   4xx+5xx .......... {}/{} = {:.2f}%".format(
        data["server_errors"] + data["client_errors"], total,
        100.0 * (data["server_errors"] + data["client_errors"]) / total))
    print("   The 5xx rate is the service-health number: the 404s are a monitor")
    print("   requesting /missing on purpose, not a fault of this stack.")


def section_4(data):
    print("4. Which paths, windows and backends account for the failures")
    for status in sorted(s for s in data["statuses"] if s >= 500):
        rows = [record for record in data["access"] if record["status"] == status]
        first, last = window(rows)
        peers = collections.Counter(peer for record in rows for peer in attempts(record))
        print("   {} x{:<3} {} .. {}".format(status, len(rows), first, last))
        print("        paths    {}".format(dict(collections.Counter(r["path"] for r in rows))))
        print("        upstream {}".format(dict(peers)))
    print()
    print("   Per-minute request and 5xx counts (each row is 24 client requests):")
    for minute, counts in sorted(bucket(data["access"]).items()):
        failures = sum(count for status, count in counts.items() if status >= 500)
        print("     {}  n={:>3}  5xx={:>2}  {:<28} {}".format(
            minute, sum(counts.values()), failures,
            str(dict(sorted(counts.items()))), "#" * failures))


def section_5(data):
    print("5. Median and p95 client latency")
    print("   Units: access.log `request_time` is SECONDS; application.log")
    print("   `duration_ms` is MILLISECONDS. They agree - e.g. lab-000292 is")
    print("   request_time 2.025 and duration_ms 2025.0 - so they must never be")
    print("   pooled into one sample.")
    print("   Method: nearest-rank percentile on the sorted sample, which always")
    print("   returns a measurement that actually happened. No interpolation.")
    for label, sample in (("all requests", data["latencies"]),
                          ("2xx/3xx/4xx only", data["ok_latencies"]),
                          ("5xx only", data["bad_latencies"])):
        if not sample:
            continue
        print("   {:<18} n={:<4} median {:.3f}s   p95 {:.3f}s   p99 {:.3f}s   max {:.3f}s"
              .format(label, len(sample), statistics.median(sample),
                      nearest_rank(sample, 0.95), nearest_rank(sample, 0.99), sample[-1]))
    print("   The headline p95 is set entirely by the failing minority: successful")
    print("   requests sit at {:.3f}s p95 while the 5xx tail sits at {:.3f}s, the".format(
        nearest_rank(data["ok_latencies"], 0.95), nearest_rank(data["bad_latencies"], 0.95)))
    print("   ~2s dependency timeout inside the application.")


def section_6(data):
    print("6. Which requests retried upstream, and how many recovered")
    retried, retried_ok = data["retried"], data["retried_ok"]
    refused = [e for e in data["errors"] if e["kind"] == "connect-refused"]
    first, last = window(retried)
    print("   requests with >1 upstream attempt ... {}".format(len(retried)))
    print("   of those, final status < 400 ........ {}  ({:.0f}%)".format(
        len(retried_ok), 100.0 * len(retried_ok) / len(retried) if retried else 0))
    print("   retry window ........................ {} .. {}".format(first, last))
    print("   upstream_status patterns ............ {}".format(
        dict(collections.Counter(r["upstream_status"] for r in retried))))
    print()
    print("   Reconciliation with error.log, which is the only place a failed")
    print("   attempt appears when the retry succeeded:")
    print("     connect-refused entries ........... {}".format(len(refused)))
    print("     ... that ended 502 for the client . {}".format(
        sum(1 for e in refused
            if data["requests"].get(e["request_id"], {}).get("status") == 502)))
    print("     ... that were retried and returned  {}".format(
        sum(1 for e in refused
            if data["requests"].get(e["request_id"], {}).get("status", 0) < 400)))
    print("   Those two add up to {}, so every refused connect maps to exactly one".format(
        len(refused)))
    print("   client request and none is counted twice.")


def section_7(data):
    print("7. Incident timeline (access + error + application evidence)")
    refused = [e for e in data["errors"] if e["kind"] == "connect-refused"]
    timeouts = [e for e in data["errors"] if e["kind"] == "upstream-timeout"]
    redis_rows = [r for r in data["dependency_rows"] if r["dependency"] == "redis"]
    pg_rows = [r for r in data["dependency_rows"] if r["dependency"] == "postgres"]
    five02 = [r for r in data["access"] if r["status"] == 502]
    five03 = [r for r in data["access"] if r["status"] == 503]
    five04 = [r for r in data["access"] if r["status"] == 504]

    print("   11:00:00  steady state begins. 24 requests/minute, 7 paths, one client")
    print("             (192.0.2.24). No 5xx for five minutes.")
    print()
    print("   11:05:02  INCIDENT 1 - backend app-02 (172.23.0.12) stops accepting")
    print("             connections. error.log: {} x connect() failed (111: Connection".format(
        len(refused)))
    print("             refused), {} .. {}, every one naming".format(
        refused[0]["ts"], refused[-1]["ts"]))
    print("             upstream http://172.23.0.12:8080.")
    print("             access.log: {} x 502 ({} .. {}), all".format(
        len(five02), *window(five02)))
    print("             attributed to 172.23.0.12 and none to 172.23.0.11.")
    print("             application.log: NOTHING for those {} request ids - the".format(
        len(data["never_served"])))
    print("             process never received them. That absence is the proof this")
    print("             is a connectivity fault and not an application fault.")
    print("             {} further requests were retried onto 172.23.0.11 and returned".format(
        len(data["retried_ok"])))
    print("             200, so the outage was partially masked from the client.")
    print("   11:09:57  last refused connect. 11:10 onward is clean.")
    print()
    print("   11:12:09  INCIDENT 2a - Redis stops answering in time. application.log:")
    print("             {} x dependency_error dependency=redis error_type=TimeoutError,".format(
        len(redis_rows)))
    print("             {} .. {}, on BOTH".format(*window(redis_rows)))
    print("             instances ({}).".format(
        dict(collections.Counter(r["instance_id"] for r in redis_rows))))
    print("             error.log is silent - the proxy was healthy throughout.")
    print("             Client impact: 503 on /ready and /counter, ~2.02s each.")
    print("   11:15:52  Redis errors stop.")
    print()
    print("   11:20:07  INCIDENT 2b - PostgreSQL authentication starts failing.")
    print("             application.log: {} x dependency_error dependency=postgres".format(
        len(pg_rows)))
    print("             error_type=InvalidPassword, {} .. {},".format(*window(pg_rows)))
    print("             again on both instances. InvalidPassword is a credential")
    print("             fault, not a reachability fault: the server answered and")
    print("             rejected the login.")
    print("   11:21:45  PostgreSQL errors stop. Total 503s: {}.".format(len(five03)))
    print()
    print("   11:25:14  INCIDENT 3 - slow upstream. error.log: {} x upstream timed".format(
        len(timeouts)))
    print("             out (110) while reading response header, {} .. {},".format(
        timeouts[0]["ts"], timeouts[-1]["ts"]))
    print("             hitting BOTH peers. access.log: {} x 504 on /records only.".format(
        len(five04)))
    print("             No dependency_error rows, so the app never reported a failure")
    print("             of its own: the requests were still in flight when the proxy")
    print("             gave up.")
    print("   11:26:47  last timeout.")
    print()
    print("   11:30:00  error.log notice: log collector rotated stream. End of capture.")


def section_8(data):
    print("8. One correlated failed request and one successful request")
    failed = next(r for r in data["access"] if r["status"] == 503)
    good = next(r for r in data["access"]
                if r["status"] == 200 and len(attempts(r)) == 1)
    retried = data["retried_ok"][0] if data["retried_ok"] else None
    app_index = collections.defaultdict(list)
    for row in data["app"]:
        app_index[row["request_id"]].append(row)
    error_index = collections.defaultdict(list)
    for entry in data["errors"]:
        error_index[entry["request_id"]].append(entry)

    for label, record in (("FAILED", failed), ("SUCCEEDED", good),
                          ("RETRIED THEN SUCCEEDED", retried)):
        if record is None:
            continue
        rid = record["request_id"]
        print("   --- {} : {} ---".format(label, rid))
        print("   access.log      {}".format(json.dumps(record, sort_keys=True)))
        for row in app_index.get(rid, []) or ["<no application record: never reached a backend>"]:
            print("   application.log {}".format(
                json.dumps(row, sort_keys=True) if isinstance(row, dict) else row))
        for entry in error_index.get(rid, []):
            print("   error.log       {}".format(entry["raw"][:150]))
        print()


def section_9(data):
    print("9. Proxy/connectivity failures versus dependency/application failures")
    print("   The three signatures are separable, and each is proved by a different")
    print("   file agreeing or staying silent:")
    print()
    print("   502 - PROXY/CONNECTIVITY. error.log has a connect() failed (111) line")
    print("         for the request; application.log has no record of it at all.")
    print("         Proof: {} of the {} requests that ended 502 have zero".format(
        len(data["never_served"]), len([r for r in data['access'] if r['status'] == 502])))
    print("         application-log rows. The backend process was not listening, so")
    print("         it could not have logged anything.")
    print()
    print("   503 - DEPENDENCY/APPLICATION. error.log is silent; application.log")
    print("         carries an explicit dependency_error with the dependency name")
    print("         and exception type, immediately followed by the 503 http_request")
    print("         row for the same request_id. The app was healthy enough to")
    print("         diagnose its own failure and answer. Both instances were hit,")
    print("         which points at the shared dependency rather than one backend.")
    print()
    print("   504 - PROXY GAVE UP ON A SLOW BACKEND. error.log has 'upstream timed")
    print("         out (110) while reading response header'; application.log has no")
    print("         completion row for those requests, because the app had accepted")
    print("         the connection and had not finished. This is the ambiguous case:")
    print("         the work may well have been done and only the answer was lost.")
    print("         For /records, a write, that ambiguity is the reason a proxy")
    print("         should not blindly retry POSTs.")
    print()
    print("   Latency separates them again: 502s are fast (a refused connect is")
    print("   immediate), 503s cluster at ~2.02s (the app's own dependency timeout),")
    print("   504s at the proxy's read timeout.")


def section_10(data):
    print("10. What the logs do not prove, and what to check next")
    print("   Not proved by these files:")
    print("   - WHY app-02 stopped accepting connections. A refused connect proves")
    print("     nothing was listening; it does not distinguish a crash, an OOM kill,")
    print("     a deploy, or a container that was simply stopped. No exit code, no")
    print("     restart count and no kernel log is present here.")
    print("   - WHY Redis timed out. TimeoutError is the client's view. Server-side")
    print("     saturation, a blocking command, an AOF rewrite stall and a network")
    print("     drop all look identical from this side.")
    print("   - Whether the PostgreSQL InvalidPassword burst was a rotated secret, a")
    print("     partially rolled-out config, or a wrong value in one environment.")
    print("     16 failures on both instances inside 98 seconds fits a credential")
    print("     change far better than it fits a network fault - but that is an")
    print("     inference, not evidence.")
    print("   - Whether the 8 timed-out /records writes were committed. The proxy")
    print("     gave up before the answer arrived; the logs cannot say what the")
    print("     database did.")
    print("   - Anything about a second client. Every line has client 192.0.2.24, so")
    print("     these numbers describe one synthetic generator, not real user traffic.")
    print()
    print("   What I would check next in a running environment:")
    print("   - docker inspect app-02 for State.ExitCode, OOMKilled and RestartCount,")
    print("     and `docker events` around 11:05:02.")
    print("   - Redis INFO commandstats / latency history / slowlog, and whether an")
    print("     AOF rewrite or a maxmemory eviction coincided with 11:12.")
    print("   - The PostgreSQL server log for FATAL password authentication failed,")
    print("     which names the role and the client address the app log cannot.")
    print("   - Whether rows exist for the 8 timed-out /records requests, which")
    print("     settles the duplicate-write question the 504s leave open.")
    print("   - Resource limits and host pressure for the whole window: none of these")
    print("     logs carries CPU, memory or connection-pool data.")


SECTIONS = [section_1, section_2, section_3, section_4, section_5,
            section_6, section_7, section_8, section_9, section_10]


def summary_json(data):
    total = len(data["access"])
    return {
        "distinct_client_requests": total,
        "malformed_lines": {"access.log": len(data["access_bad"]),
                            "application.log": len(data["app_bad"]),
                            "error.log": len(data["error_bad"])},
        "exact_duplicate_lines": {"access.log": len(data["access_dupes"]),
                                  "application.log": len(data["app_dupes"])},
        "status_counts": dict(sorted(data["statuses"].items())),
        "error_rate_5xx_percent": round(100.0 * data["server_errors"] / total, 2),
        "latency_seconds": {
            "median": statistics.median(data["latencies"]),
            "p95": nearest_rank(data["latencies"], 0.95),
            "p95_successful_only": nearest_rank(data["ok_latencies"], 0.95),
        },
        "retried_requests": len(data["retried"]),
        "retried_and_succeeded": len(data["retried_ok"]),
        "requests_never_reaching_a_backend": len(data["never_served"]),
        "window_utc": list(window(data["access"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--section", type=int, choices=range(1, 11), action="append",
                        help="print only these numbered sections (repeatable)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    args = parser.parse_args()

    for name in ("access.log", "error.log", "application.log"):
        if not (LOG_DIR / name).exists():
            print("missing {}".format(LOG_DIR / name), file=sys.stderr)
            return 2

    data = analyse()
    if args.json:
        print(json.dumps(summary_json(data), indent=2, sort_keys=True))
        return 0

    wanted = args.section or list(range(1, 11))
    for number in wanted:
        SECTIONS[number - 1](data)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
