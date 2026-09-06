# Log analysis

Three supplied logs, all synthetic lab data, all read **read-only** - the originals in
`logs/` are unchanged and are still the files committed in the `starter-v2.0.0` baseline.

Every number below is produced by [`scripts/analyze_logs.py`](scripts/analyze_logs.py).
Nothing here was counted by hand. Re-running the script regenerates the whole report:

```bash
python scripts/analyze_logs.py               # the full report
python scripts/analyze_logs.py --section 7   # one numbered question
python scripts/analyze_logs.py --json        # machine-readable summary
```

Saved output: [`evidence/18-log-analysis-output.txt`](evidence/18-log-analysis-output.txt).

Confirm the originals were never edited:

```bash
git diff --stat starter-v2.0.0 -- logs/     # no output
```

---

## Commands / scripts

The Python script is the reference implementation, but the headline numbers are also
reproducible with ordinary shell tools. Every command below was run on this host
(Git Bash; `jq` is **not** installed here, so nothing depends on it) and agrees with the
script:

```bash
# distinct client requests -> 720
sort -u logs/access.log | grep -c '^{.*}$'

# exact duplicate lines -> 5, and which ones
sort logs/access.log | uniq -d | wc -l
sort logs/access.log | uniq -d | cut -c1-60

# refused connects -> 59      upstream timeouts -> 8
grep -c 'Connection refused' logs/error.log
grep -c 'upstream timed out' logs/error.log

# dependency failures by type -> 31 redis TimeoutError, 16 postgres InvalidPassword
grep -o '"dependency": "[a-z]*", "error_type": "[A-Za-z]*"' logs/application.log \
  | sort | uniq -c

# final status counts -> 615/10/40/47/8
sort -u logs/access.log | grep -o '"status":[0-9]*' | sort | uniq -c

# retried requests (a comma inside the upstream field) -> 19
sort -u logs/access.log | grep -c '"upstream":"[^"]*, '

# 5xx per minute -> the four bursts in section 4
sort -u logs/access.log | grep '"status":5[0-9][0-9]' | cut -c26-30 | sort | uniq -c
```

The last one relies on `access.log` being written as compact JSON with a fixed field
order, so `HH:MM` is always at columns 26-30. That holds for these files and is checked
against the script, but it is the sort of assumption that breaks the first time a writer
adds a field - which is why the script parses instead of slicing.

**A trap worth naming.** The obvious count is wrong:

```bash
sort -u logs/access.log | grep -c '^{'        # 721  <- WRONG
sort -u logs/access.log | grep -c '^{.*}$'    # 720  <- correct
```

The truncated line on `access.log:311` also begins with `{`, so any filter that tests
only "looks like JSON" silently counts it as a request. Requiring the closing brace
excludes it. This is the same failure mode as the `error.log` `[notice]` line in
section 1, from the opposite direction: one parser dropped a line it should have kept,
the other kept a line it should have dropped. Both were found by making the two methods
disagree, which is the point of doing it twice.

### Parse exclusions, stated before any count

1. **One access-log line is one client request.** NGINX writes the access line once, when
   the client response completes, however many upstream attempts it took. A retry shows
   up as extra comma-separated values inside `upstream` / `upstream_status`, never as an
   extra line.
2. **Exact duplicate lines are collapsed.** They are log-shipping artefacts, not extra
   requests. Deduplication is on the **whole record**, never on `request_id` alone,
   because one request legitimately produces several application-log records.
3. **Truncated lines are excluded and reported**, never guessed at or repaired.

---

## Results

### 1. UTC interval covered; valid, malformed and duplicate lines per file

| File | Lines | Unique valid | Malformed | Exact duplicates | Interval (UTC) |
|---|---|---|---|---|---|
| `access.log` | 726 | 720 | 1 | 5 | 11:00:00.015 – 11:29:57.578 |
| `application.log` | 730 | 727 | 1 | 2 | 11:00:00.015 – 11:29:57.578 |
| `error.log` | 68 | 68 | 0 | 0 | 11:05:02 – 11:30:00 |

All on **2026-08-20**, all UTC (`logs/README.md`). Access and application timestamps are
ISO-8601 with milliseconds; `error.log` uses NGINX's `YYYY/MM/DD HH:MM:SS`, second
resolution only - which is why correlation is done on `request_id`, not on time.

The malformed lines are truncations, not corruption:

```
logs/access.log:311      {"timestamp":"2026-08-20T11:12:48Z","request_id":
logs/application.log:401 {"timestamp":"2026-08-20T11:17:00Z","event":
```

Both stop mid-record. Neighbouring lines are intact, so this is a writer that was cut
off, not a damaged file. They are dropped: the surviving fragment carries no status, no
upstream and no duplicate-detectable identity, so anything inferred from it would be
invention. Their effect is bounded - at most one client request each, ≤0.28% of the total.

The duplicates are byte-identical repeats: `lab-000121`, `-000241`, `-000361`, `-000481`,
`-000601` in `access.log` (note the exact 120-request spacing, which is a shipper
artefact, not user behaviour) and `lab-000181`, `-000421` in `application.log`.

**Reading the error.log parser was worth it.** My first version required the
`*<connection>` field every NGINX error line carries and reported `67 parsed, 1 unparsed`.
The unparsed line was real and important - `11:30:00 [notice] log collector rotated
stream`, the marker for the end of the capture. `[notice]` lines have no connection id.
Making that field optional took the count to 68/68 and gave the timeline its closing
entry. A parser that silently drops what it does not expect will lose exactly the line
that explains the gap.

### 2. Distinct client requests, and how retries avoid double counting

**720 distinct client requests.**

```
unique valid access lines ............. 720
distinct request_id values ............ 720
upstream attempts recorded ............ 739   <- NOT the request count
error.log lines carrying a request_id .. 67   <- NOT the request count
```

19 requests carry two upstream attempts. Counting attempts would report **739** requests
instead of 720 - a 2.6% inflation, concentrated entirely inside the outage window, which
is exactly where an inflated denominator would flatter the error rate.

An independent cross-check from the other side of the proxy, which does not reuse the
access log at all:

```
unique application http_request records ....... 680
requests with no application record at all .....  40
                                                ----
                                                 720
```

The two methods agree exactly. The 40 with no application record are precisely the
requests that ended 502 - the backend never received them, so it could not log them.
That is the same number arrived at twice from independent files, and it is what makes
the 720 trustworthy rather than merely plausible.

### 3. Final client status counts and error rate

| Status | Count | Share |
|---|---|---|
| 200 | 615 | 85.42% |
| 404 | 10 | 1.39% |
| 502 | 40 | 5.56% |
| 503 | 47 | 6.53% |
| 504 | 8 | 1.11% |

**Denominator: 720 distinct client requests** - deduplicated access lines, malformed
lines excluded, retries counted once, measured at the edge where the client saw them.

- **5xx error rate: 95/720 = 13.19%** ← the service-health number
- 4xx rate: 10/720 = 1.39%
- 4xx+5xx: 105/720 = 14.58%

The 404s are all `/missing`, one roughly every three minutes across the whole window
including the quiet periods. That is a monitor probing a deliberately absent route, not a
fault of this stack, so quoting 14.58% as "the error rate" would overstate the incident
by a tenth. Both numbers are given; the denominator is the same for both.

### 4. Paths, time windows and backends behind the failures

| Status | n | Window (UTC) | Paths | Upstream(s) |
|---|---|---|---|---|
| 502 | 40 | 11:05:02 – 11:09:57 | `/health` 10, `/records` 10, `/counter` 10, `/` 10 | `172.23.0.12:8080` only |
| 503 | 47 | 11:12:09 – 11:21:45 | `/ready` 23, `/counter` 16, `/records` 8 | both peers (24 / 23) |
| 504 | 8 | 11:25:14 – 11:26:47 | `/records` only | both peers (4 / 4) |

Three observations that identify the fault class before any log text is read:

- **502s name one peer and only one.** `172.23.0.12` took every one; `172.23.0.11` took
  none. A single-backend fault.
- **503s are split almost evenly across both peers**, and hit exactly the three endpoints
  that touch a dependency (`/ready`, `/counter`, `/records`) while never touching
  `/health`, `/instance` or `/`, which do not. A shared-dependency fault, and the path
  distribution alone tells you which dependency layer.
- **504s hit both peers but only `/records`** - the heaviest endpoint. A slowness fault,
  not an availability fault.

Per-minute request and 5xx counts (traffic is a flat 24 requests/minute throughout, so
the 5xx column is directly comparable across rows):

```
11:00  n= 24  5xx= 0
11:01  n= 24  5xx= 0
11:02  n= 24  5xx= 0
11:03  n= 24  5xx= 0
11:04  n= 24  5xx= 0
11:05  n= 24  5xx= 8  ########   <- incident 1 begins
11:06  n= 24  5xx= 8  ########
11:07  n= 24  5xx= 8  ########
11:08  n= 24  5xx= 8  ########
11:09  n= 24  5xx= 8  ########
11:10  n= 24  5xx= 0
11:11  n= 24  5xx= 0
11:12  n= 24  5xx= 8  ########   <- incident 2a begins (Redis)
11:13  n= 24  5xx= 7  #######
11:14  n= 24  5xx= 8  ########
11:15  n= 24  5xx= 8  ########
11:16  n= 24  5xx= 0
11:17  n= 24  5xx= 0
11:18  n= 24  5xx= 0
11:19  n= 24  5xx= 0
11:20  n= 24  5xx= 8  ########   <- incident 2b begins (PostgreSQL)
11:21  n= 24  5xx= 8  ########
11:22  n= 24  5xx= 0
11:23  n= 24  5xx= 0
11:24  n= 24  5xx= 0
11:25  n= 24  5xx= 4  ####       <- incident 3 begins (timeouts)
11:26  n= 24  5xx= 4  ####
11:27  n= 24  5xx= 0
11:28  n= 24  5xx= 0
11:29  n= 24  5xx= 0
```

Failures arrive in four tight bursts separated by fully clean minutes. Nothing degrades
gradually and nothing overlaps, so the incidents can be reasoned about one at a time.

### 5. Median and p95 client latency

**Units.** `access.log.request_time` is in **seconds**; `application.log.duration_ms` is
in **milliseconds**. They measure the same thing from two sides and agree -
`lab-000292` is `request_time 2.025` and `duration_ms 2025.0` - so they must never be
pooled into one sample. Everything below uses the edge measurement in seconds, because
that is what the client experienced.

**Method.** Nearest-rank percentile on the sorted sample: `p = value at ceil(0.95 × n)`.
No interpolation, so every figure quoted is a measurement that actually occurred. n=720.

| Sample | n | Median | p95 | p99 | Max |
|---|---|---|---|---|---|
| All requests | 720 | **0.054s** | **2.001s** | 2.025s | 2.025s |
| 2xx/3xx/4xx only | 625 | 0.055s | 0.093s | 0.120s | 0.120s |
| 5xx only | 95 | 0.041s | 2.025s | 2.025s | 2.025s |

The headline p95 of 2.001s is set **entirely** by the failing 13%: successful requests
never exceeded 0.120s. Quoting only "p95 = 2.0s" would suggest a system that is slow for
everyone, when in fact it was fast for everyone it served and ~2.02s for everyone it
failed - the application's own ~2s dependency timeout, visible as a near-constant value
rather than a distribution.

Note the 5xx **median** is 0.041s, *lower* than the overall median. That is the 502s: a
refused connect fails immediately. The 5xx sample is bimodal - instant refusals and ~2s
timeouts - so its median and p95 describe two different failure modes, and neither number
means anything without the split in section 4.

### 6. Which requests retried, and how many recovered

- Requests with more than one upstream attempt: **19**
- Of those, final status < 400: **19 (100%)**
- Retry window: 11:05:07.620 – 11:09:37.620 (inside incident 1 only)
- `upstream_status` pattern: `"502, 200"` for all 19 - first peer failed, second answered

Reconciliation with `error.log`, which is the **only** place a failed attempt survives
when the retry succeeded (the access line shows the final 200):

```
connect-refused entries in error.log ....... 59
  ... whose client request ended 502 ....... 40
  ... whose client request was retried, 200 . 19
                                             ---
                                              59
```

Every refused connect maps to exactly one client request, and 40 + 19 = 59 exactly. This
is the check that proves the deduplication in section 2 is right rather than merely
self-consistent: the retried requests are counted once as requests and once as upstream
failures, in two different files, without either total drifting.

32% of the connect failures were masked from the client by a retry. The other 68% were
not - the proxy did not retry every failure, which is precisely the
`proxy_next_upstream`/`max_fails` gap I found and fixed in the current environment
(`troubleshooting.md` entries 11 and 17).

---

## Timeline and correlated examples

### 7. Incident timeline

All times UTC, 2026-08-20. Each entry cites which file supports it.

**11:00:00 — steady state.** 24 requests/minute, seven paths, one client (192.0.2.24),
two backends. No 5xx for five minutes.

**11:05:02 — INCIDENT 1: backend `app-02` (172.23.0.12) stops accepting connections.**
- `error.log`: 59 × `connect() failed (111: Connection refused)`, 11:05:02 → 11:09:57,
  every one naming `upstream: http://172.23.0.12:8080`. Not one names `.11`.
- `access.log`: 40 × 502, same window, upstream `172.23.0.12:8080` exclusively.
- `application.log`: **nothing at all** for those 40 request ids. The absence is the
  evidence - a process that is not listening cannot log.
- 19 further requests were retried onto `172.23.0.11` and returned 200, so roughly a
  third of the outage was invisible to the client.
- **11:09:57** last refused connect; 11:10 onward is clean. Duration ≈ 4m55s.

**11:12:09 — INCIDENT 2a: Redis stops answering in time.**
- `application.log`: 31 × `dependency_error dependency=redis error_type=TimeoutError`,
  11:12:09.524 → 11:15:52.024, on **both** instances (app-02 16, app-01 15).
- `error.log`: **silent**. The proxy was healthy throughout - this never reached it.
- Client impact: 503 on `/ready` and `/counter`, ~2.02s each.
- **11:15:52** last Redis error. Duration ≈ 3m43s.

**11:20:07 — INCIDENT 2b: PostgreSQL authentication starts failing.**
- `application.log`: 16 × `dependency_error dependency=postgres
  error_type=InvalidPassword`, 11:20:07.540 → 11:21:45.040, again on both instances.
- `InvalidPassword` is a **credential** fault, not a reachability fault: the server
  answered and rejected the login. A network problem would have produced a connect
  error, and a down server would have produced a refused connect in `error.log`.
- Client impact: 503 on `/ready` and `/records`.
- **11:21:45** last PostgreSQL error. Duration ≈ 1m38s.

**11:25:14 — INCIDENT 3: slow upstream.**
- `error.log`: 8 × `upstream timed out (110: Operation timed out) while reading response
  header`, 11:25:14 → 11:26:47, hitting **both** peers.
- `access.log`: 8 × 504, `/records` only.
- `application.log`: **no completion row** for those requests. The app had accepted the
  connection and had not finished when the proxy gave up.
- **11:26:47** last timeout. Duration ≈ 1m33s.

**11:30:00 — `error.log` `[notice] log collector rotated stream`.** End of capture. The
absence of data after this is rotation, not silence, and must not be read as recovery.

Two incidents (2a, 2b) hit both backends and were reported by the application itself;
two (1, 3) were visible to the proxy. Nothing overlaps. Total impaired time ≈ 11m49s of
a 30-minute window, but only 13.19% of requests failed, because two of the four
incidents degraded a subset of endpoints rather than the whole service.

### 8. Correlated examples

**A failed request — `lab-000292`, 11:12:09 (dependency failure):**

```
access.log      {"timestamp":"2026-08-20T11:12:09.525Z","request_id":"lab-000292",
                 "method":"GET","path":"/ready","status":503,
                 "upstream":"172.23.0.12:8080","upstream_status":"503",
                 "request_time":2.025,"client":"192.0.2.24"}

application.log {"timestamp":"2026-08-20T11:12:09.524Z","level":"ERROR",
                 "event":"dependency_error","request_id":"lab-000292",
                 "instance_id":"app-02","dependency":"redis",
                 "error_type":"TimeoutError"}
application.log {"timestamp":"2026-08-20T11:12:09.525Z","level":"WARN",
                 "event":"http_request","request_id":"lab-000292",
                 "instance_id":"app-02","method":"GET","path":"/ready",
                 "status":503,"duration_ms":2025.0}

error.log       (no entry - the proxy saw a normal, if unhappy, response)
```

The chain is complete: the dependency error is logged 1ms before the response,
`instance_id app-02` matches upstream `172.23.0.12`, and `duration_ms 2025.0` matches
`request_time 2.025`. The 503 was the application's own considered answer.

**A successful request — `lab-000002`, 11:00:02:**

```
access.log      {"timestamp":"2026-08-20T11:00:02.532Z","request_id":"lab-000002",
                 "method":"GET","path":"/health","status":200,
                 "upstream":"172.23.0.12:8080","upstream_status":"200",
                 "request_time":0.032,"client":"192.0.2.24"}

application.log {"timestamp":"2026-08-20T11:00:02.532Z","level":"INFO",
                 "event":"http_request","request_id":"lab-000002",
                 "instance_id":"app-02","method":"GET","path":"/health",
                 "status":200,"duration_ms":32.0}
```

One upstream, one attempt, identical timestamps, 32.0ms == 0.032s.

**A retried request — `lab-000124`, 11:05:07 — all three files, one request:**

```
error.log       2026/08/20 11:05:07 [error] 31#31: *124 connect() failed
                (111: Connection refused) while connecting to upstream,
                request_id=lab-000124, request: "GET /ready HTTP/1.1",
                upstream: "http://172.23.0.12:8080/ready"

access.log      {"timestamp":"2026-08-20T11:05:07.620Z","request_id":"lab-000124",
                 "method":"GET","path":"/ready","status":200,
                 "upstream":"172.23.0.12:8080, 172.23.0.11:8080",
                 "upstream_status":"502, 200","request_time":0.12}

application.log {"timestamp":"2026-08-20T11:05:07.620Z","level":"INFO",
                 "event":"http_request","request_id":"lab-000124",
                 "instance_id":"app-01","method":"GET","path":"/ready",
                 "status":200,"duration_ms":120.0}
```

This single request is the whole double-counting problem in one place. It appears in
`error.log` as a failure, in `access.log` as a success, and in `application.log` on the
*other* instance from the one that failed. It is **one** client request, not two or
three. `app-01` logged it because `app-01` served it; `172.23.0.12` never did.

---

## Conclusions and limits

### 9. Proxy/connectivity versus dependency/application

Each class is proved by a different file speaking *and* another staying silent.

| | 502 | 503 | 504 |
|---|---|---|---|
| `error.log` | `connect() failed (111)` | silent | `upstream timed out (110)` |
| `application.log` | **no record at all** | `dependency_error` + 503 row | no completion row |
| Peers affected | one (`.12`) | both | both |
| Latency | instant (median 0.041s) | ~2.02s (app timeout) | proxy read timeout |
| Verdict | **proxy/connectivity** | **dependency/application** | **proxy gave up on a slow backend** |

- **502 = connectivity.** 40 of 40 have zero application-log rows. A listening process
  writes a line even when it answers 500; a process that is not listening writes nothing.
  The silence is not weak evidence here, it is the strongest evidence available.
- **503 = dependency, and the app is healthy.** It caught the exception, named the
  dependency and the exception type, answered within its own timeout, and the proxy never
  noticed a problem. Both instances failing simultaneously rules out either backend and
  points at the shared datastore.
- **504 = ambiguous by construction.** The proxy stopped waiting; the app never said
  whether it finished. All eight were `POST`-shaped work on `/records`. Whether those
  writes committed is not knowable from these files - and that ambiguity is exactly why
  the environment I built does not enable `proxy_next_upstream non_idempotent`
  (`decisions.md`).

`InvalidPassword` deserves separating from the other dependency errors: a timeout means
"I could not get an answer", a rejected password means "I got a clear answer and it was
no". They need different responses - one is a capacity or network question, the other is
a configuration question - and the starter environment I repaired had a **real**
credential mismatch of the same shape (`troubleshooting.md` entry 06), which is why this
one stood out.

### 10. What the logs do not prove, and what to check next

**Not proved by these files:**

- **Why `app-02` stopped listening.** A refused connect proves nothing was bound to the
  port. It does not distinguish a crash, an OOM kill, a rolling deploy, or a container
  that was stopped on purpose. No exit code, restart count or kernel message is present.
- **Why Redis timed out.** `TimeoutError` is the *client's* view. Server saturation, a
  blocking command, an AOF rewrite stall and a dropped network path are indistinguishable
  from this side.
- **Why PostgreSQL rejected the password.** A rotated secret, a half-rolled-out config
  and a wrong value in one environment all look identical. 16 failures on both instances
  inside 98 seconds fits a credential change better than a network fault - but that is
  an inference, and it is labelled as one.
- **Whether the 8 timed-out `/records` writes committed.** The logs end at the proxy's
  patience, not at the database's behaviour.
- **Anything about real users.** Every one of the 720 lines carries client
  `192.0.2.24`, a flat 24 requests/minute, and paths in a fixed rotation. This is one
  synthetic generator. No conclusion about user-visible impact, concurrency, or
  peak-hour behaviour can be drawn from it.
- **Anything after 11:30:00**, where the collector rotated.

**What I would check next in a running environment, in priority order:**

1. `docker inspect app-02` for `State.ExitCode`, `OOMKilled` and `RestartCount`, plus
   `docker events --since` around 11:05:02. This settles incident 1 in one command.
2. PostgreSQL server log for `FATAL: password authentication failed for user`, which
   names the role and client address the application log cannot, and `pg_hba.conf` /
   secret rotation history around 11:20.
3. Redis `INFO commandstats`, `SLOWLOG GET`, `LATENCY HISTORY`, and whether an AOF
   rewrite or a `maxmemory` eviction coincided with 11:12.
4. Whether rows exist for the 8 timed-out `/records` requests - the one question with a
   data-correctness consequence rather than an availability one.
5. Container resource limits and host pressure across the window. None of these three
   logs carries CPU, memory, file-descriptor or connection-pool data, and two of the four
   incidents (a timeout and a slowdown) are exactly what resource exhaustion looks like.
6. Whether the two truncated lines and the seven duplicated lines share a cause in the
   log shipper. Both are small here, but a shipper that truncates under load will
   truncate most during the incident you most need to read.
