<img src="assets/barq-logo.svg" alt="BARQ Systems" width="180">

# BARQ DevOps internship task - repaired environment

A Flask API behind NGINX with PostgreSQL and Redis, delivered from the intentionally
broken starter in [`assessment/TASK.md`](assessment/TASK.md).

**15 faults** were found in the baseline, each reproduced, fixed and re-tested in its own
commit. Two further bugs introduced by my own hardening were found by measurement and are
documented as mine. `validate.py` runs **46 checks** and exits non-zero if any fails.

| Document | What is in it |
|---|---|
| [`troubleshooting.md`](troubleshooting.md) | The investigation journal - 17 entries, four wrong hypotheses, every command and its real output |
| [`log_analysis.md`](log_analysis.md) | All ten log questions, answered by [`scripts/analyze_logs.py`](scripts/analyze_logs.py) |
| [`decisions.md`](decisions.md) | 12 decisions with the alternative rejected and what it costs |
| [`security_review.md`](security_review.md) | 14 findings - 10 fixed, **4 still open** in this submission |
| [`AI_USAGE.md`](AI_USAGE.md) | AI was used extensively; where, what was rejected, how it was verified |
| [`docs/EVIDENCE_INDEX.md`](docs/EVIDENCE_INDEX.md) | requirement -> file -> commit -> video timestamp |
| [`architecture.png`](architecture.png) / [`.pdf`](architecture.pdf) | Request flow, ports, networks, storage, health relationships |
| [`evidence/`](evidence/) | Raw captured output for every claim above |

---

## Requirements

- Docker with Compose v2 (developed on Docker Desktop 29.3.1, Linux containers, WSL2)
- Python 3.12 on the host, for `validate.py`, `failure_test.py` and the log analyser
- Bash for `backup.sh` / `restore.sh` (Git Bash on Windows is fine)
- Free host ports: **8080** before the video, **8090** after. Nothing else is published.

---

## Setup

```bash
git clone <this repository>
cd barq-devops-task

# 1. Create your .env from the template and put a real password in it.
#    .env is git-ignored. .env.example ships a placeholder, never a usable secret.
cp .env.example .env

# 2. Generate a password and write it into .env (any strong value works).
python -c "import secrets,string;a=string.ascii_letters+string.digits;print('POSTGRES_PASSWORD='+''.join(secrets.choice(a) for _ in range(24)))"
#    ...then replace the POSTGRES_PASSWORD line in .env with the output.

# 3. Sanity-check the configuration before building anything.
docker compose config >/dev/null && echo "compose config OK"
```

> **The password must be set before the first start.** PostgreSQL only reads it when it
> initialises an empty volume. Changing it later has no effect on an existing volume - you
> would have to `docker compose down --volumes`, which destroys the data.

## Build and start

```bash
docker compose -p barq-assessment up -d --build --wait
docker compose -p barq-assessment ps
```

`--wait` blocks until every health check passes, so a green return means the stack is
genuinely ready, not merely started. Expected:

```
NAME       STATUS                    PORTS
app-01     Up (healthy)              8080/tcp
app-02     Up (healthy)              8080/tcp
nginx      Up (healthy)              127.0.0.1:8080->8080/tcp
postgres   Up (healthy)              5432/tcp
redis      Up (healthy)              6379/tcp
```

Only `nginx` has a host binding. `8080/tcp` without a `127.0.0.1:` prefix means the port
is exposed inside the Docker network only - it is **not** reachable from the host.

## Stop, restart and clean up

```bash
# stop, keeping data
docker compose -p barq-assessment stop

# start again
docker compose -p barq-assessment start

# remove containers and networks, KEEP the volumes (data survives)
docker compose -p barq-assessment down

# remove everything including the database and cache volumes - DESTRUCTIVE
docker compose -p barq-assessment down --volumes
```

`down --volumes` is the only command here that destroys data. Never use it during a
persistence test. Take a backup first if you care about the contents.

---

## Test the API

```bash
BASE=http://127.0.0.1:8080

curl -i $BASE/                      # 200 + message + instance_id
curl -i $BASE/health                # 200, liveness only, no dependency call
curl -i $BASE/ready                 # 200 when PostgreSQL AND Redis answer, else 503
curl -i $BASE/instance              # 200, identity also in the X-Instance-ID header
curl -i -H 'Content-Type: application/json' \
     -d '{"title":"Persistence proof"}' $BASE/records     # 201
curl -s $BASE/records                                     # the list
curl -s $BASE/counter                                     # Redis counter, increments
curl -s -o /dev/null -w '%{http_code}\n' $BASE/missing     # 404
curl -s -o /dev/null -w '%{http_code}\n' \
     -H 'Content-Type: application/json' -d '{"title":""}' $BASE/records   # 400
```

Prove both backends are serving:

```bash
for i in $(seq 20); do
  curl -s -o /dev/null -D - $BASE/instance | grep -i '^X-Instance-ID'
done | sort | uniq -c
#      10 X-Instance-ID: app-01
#      10 X-Instance-ID: app-02
```

App-only unit tests (fake dependencies - these do **not** prove the environment works):

```bash
python -m venv .venv && source .venv/Scripts/activate     # or bin/activate on Linux
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v                    # 11 tests
```

---

## Validate the whole environment

```bash
python validate.py                       # 46 checks, exit 0 on success, 1 on any failure
python validate.py --url http://127.0.0.1:8090
python validate.py --timeout 120
echo $?
```

It checks public reachability, all six endpoints plus 404 and 400, PostgreSQL and Redis
readiness, that **every** discovered app instance actually serves, record create/list/
reject, counter increment, network isolation (by topology *and* by proving nginx cannot
resolve the datastore names at runtime), that only the edge publishes a host port, that
the four prohibited ports are closed, non-root uid, restart policy and memory limit per
service, PGDATA on a named volume rather than tmpfs, and Redis persistence.

Services are discovered through `docker compose ps`, so a third instance is validated
automatically with no edit.

**Validation should fail** when any of those is untrue. It has been tested negatively -
see [`evidence/14-validate-negative-tests.txt`](evidence/14-validate-negative-tests.txt):

```bash
docker stop app-02 && python validate.py --timeout 12; echo "exit=$?"   # 2 failures, exit 1
docker start app-02
docker network connect barq-assessment_backend nginx
python validate.py --timeout 12; echo "exit=$?"                          # 3 failures, exit 1
docker network disconnect barq-assessment_backend nginx
```

## Failure and recovery test

```bash
python failure_test.py                                   # default: stop app-01, 10s per phase
python failure_test.py --victim app-02 --seconds 15
python failure_test.py --min-availability 99.5
```

Three measured phases - baseline, one backend stopped, recovered - with concurrent
traffic, reporting requests, errors, availability, p50/p95 and which instance served what.
The victim is restarted in a `finally` block, so Ctrl+C still leaves the stack running.
Measured result ([`evidence/15-failure-test.txt`](evidence/15-failure-test.txt)):

```
1 baseline        GET  516/516 ok (100.00% available)  POST 73/73 ok  p50 16.0ms  p95 32.0ms
2 app-01 stopped  GET  382/382 ok (100.00% available)  POST 54/54 ok  p50 16.0ms  p95 47.0ms
3 recovered       GET  527/527 ok (100.00% available)  POST 75/75 ok  p50 16.0ms  p95 32.0ms
GET availability   baseline 100.00%   outage 100.00%   recovered 100.00%
```

---

## Backup and restore

```bash
./backup.sh                          # -> backups/barq_tasks-<UTC>.dump, verified
BACKUP_DIR=/tmp ./backup.sh          # choose the output directory

./restore.sh                         # restore the newest dump, with a confirmation prompt
./restore.sh backups/barq_tasks-20260906T213257Z.dump
FORCE=1 ./restore.sh                 # skip the prompt (CI)
```

`backup.sh` reads the dump's table of contents back with `pg_restore --list` before
reporting success - a dump nobody has read is not a backup. Both scripts resolve the
container through `docker compose -p <project> ps -q postgres`, never by bare container
name, so they cannot act on an unrelated project.

**Proving a restore actually restores** (full transcript in
[`evidence/17-backup-restore-proof.txt`](evidence/17-backup-restore-proof.txt)):

```bash
./backup.sh                                                   # dump at 210 rows
curl -sS -H 'Content-Type: application/json' \
  -d '{"title":"POST-BACKUP MARKER"}' $BASE/records            # change the data
PG=$(docker compose -p barq-assessment ps -q postgres)
docker exec $PG psql -qtAX -U barq_app -d barq_tasks -c "DELETE FROM records WHERE id = 1"
FORCE=1 ./restore.sh
curl -s $BASE/records | grep -c 'POST-BACKUP MARKER'          # 0  - the new row is gone
docker exec $PG psql -qtAX -U barq_app -d barq_tasks -c "SELECT count(*) FROM records WHERE id=1"   # 1 - the deleted row is back
```

## Proving persistence across container recreation

```bash
MARKER="persistence $(date -u +%Y%m%dT%H%M%SZ)"
curl -sS -H 'Content-Type: application/json' -d "{\"title\":\"$MARKER\"}" $BASE/records

docker inspect postgres --format '{{.Id}}'          # note the container id
docker compose -p barq-assessment rm -sf app-01 app-02 postgres     # volumes kept
docker compose -p barq-assessment up -d --wait
docker inspect postgres --format '{{.Id}}'          # a DIFFERENT id

curl -s $BASE/records | grep -c "$MARKER"           # 1  - the record survived
curl -s $BASE/counter                               # continues, does not reset to 1
```

Note `rm -sf`, not `down --volumes`: the containers are destroyed and recreated while the
named volumes stay. This is exactly the test the starter would have failed silently -
PGDATA was on a `tmpfs` while the named volume was mounted somewhere PostgreSQL never
writes (`troubleshooting.md` entry 08).

## Analysing the historical logs

```bash
python scripts/analyze_logs.py              # the full ten-question report
python scripts/analyze_logs.py --section 7  # just the incident timeline
python scripts/analyze_logs.py --json       # machine-readable summary

git diff --stat starter-v2.0.0 -- logs/     # empty: the originals are unchanged
```

## Changing the public port, and adding a third instance

```bash
# public port 8080 -> 8090
sed -i 's/^PUBLIC_PORT=.*/PUBLIC_PORT=8090/' .env
docker compose -p barq-assessment up -d nginx
curl -s http://127.0.0.1:8090/health
python validate.py --url http://127.0.0.1:8090

# add a third instance: add the app-03 service to docker-compose.yml and the
# matching upstream line to nginx/nginx.conf, then
docker compose -p barq-assessment up -d app-03
docker exec nginx nginx -t && docker exec nginx nginx -s reload
python validate.py --url http://127.0.0.1:8090       # discovers app-03 automatically
```

---

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull
request, cheapest gates first so a typo fails in seconds:

`compileall` -> `bash -n` -> `shellcheck` -> `docker compose config` -> unit tests ->
build -> `up -d --wait` -> `validate.py` -> `failure_test.py` -> backup/restore round trip
-> persistence across container recreation -> `validate.py` again. Logs are always
dumped and the stack is always torn down, including on failure. A second, non-gating job
runs Trivy against the image and a filesystem scan for secrets and misconfiguration.

CI never uses a committed secret: it mints a throwaway `POSTGRES_PASSWORD` per run.

---

## Answers to the questions in the brief

**What failed first? What proved the cause?**
`curl http://127.0.0.1:8080/health` returned `curl: (52) Empty reply from server` - not
"connection refused", so *something* accepted the TCP connection and then closed it. That
distinction is what pointed at a publish rule aimed at a dead container port rather than
at a stopped service. `docker port nginx` showed `81/tcp -> 127.0.0.1:8080` while
`docker exec nginx netstat -ltnp` showed nginx listening on `0.0.0.0:80`. Compose was
publishing to a port nothing was bound to.

**Which failed attempt taught you something?**
Four, and each changed how I worked (`AI_USAGE.md` has the table). The most useful:
I wrote up "the database is exposed on host port 15432" from reading the YAML, and
`netstat` disagreed - Docker cannot publish a host port for a container attached only to
an `internal` network, so the publish silently did nothing. Since then every isolation
and exposure claim in this repository is asserted from `netstat`, `docker port`,
`/proc/net/tcp` or `getent` **inside** the container, never from configuration text. The
second most useful: I thought the load balancer was broken when 12/12 requests hit
`app-01`. It was nginx keeping a per-worker round-robin cursor across 16 workers - a real
property of nginx, not a planted fault, and one that would have made the video's
"both backends serve" demonstration fail at random.

**What patterns did the logs reveal?**
Four tight, non-overlapping bursts in a 30-minute window at a flat 24 requests/minute:
one backend refusing connections (11:05-11:09, 502s naming one peer only), Redis timing
out (11:12-11:15, 503s on *both* peers), PostgreSQL rejecting the password (11:20-11:21,
503s on both peers), and upstream read timeouts (11:25-11:26, 504s on `/records` only).
The path distribution alone separates them: dependency failures only ever touched
`/ready`, `/counter` and `/records`, never `/health`, `/instance` or `/`.

**How did you avoid double-counting requests?**
One access-log line is one client request - NGINX writes it once, when the client
response completes, and a retry appears as an extra comma-separated value inside
`upstream`, not as an extra line. 19 requests carried two attempts, so counting attempts
would have reported 739 requests instead of 720, with the inflation sitting entirely
inside the outage window where it would have flattered the error rate. Deduplication is
on the whole record, never on `request_id`, because one request legitimately writes
several application-log rows. Verified from the other side: 680 unique application
records + 40 requests with no application record = 720, and in `error.log`, 59 refused
connects = 40 client 502s + 19 retried-and-succeeded.

**How do requests flow?**
Client -> `127.0.0.1:8090` -> nginx (`frontend`) -> round robin over app-01/02/03
(`frontend`) -> PostgreSQL and Redis by service name (`backend`). nginx is **not** on
`backend`, so it cannot resolve or reach either datastore. See
[`architecture.png`](architecture.png).

**Why these ports, networks and readiness checks?**
One published port because one entry point is one thing to defend. Two networks because
the internet-facing proxy has no business reaching the database, and `internal: true` on
`backend` also denies the datastores outbound access. `/health` and `/ready` are kept
separate so Docker probes liveness only - if Docker probed `/ready`, a Redis blip would
restart two perfectly healthy application containers and turn a recoverable dependency
incident into an application outage.

**Why these timeouts, retries, restart settings and resource limits?**
They form a ladder, each layer more patient than the one below: 2s connect (matching the
app's own dependency connect timeout), 5s read (above the worst legitimate response, so
the proxy never replaces the app's informative 503 with an opaque 504), 12s retry budget,
30s gunicorn. The 12s is the one I got wrong first - equal to `proxy_read_timeout` it made
retries impossible and produced three client-visible 504s against a hung backend; raising
it produced zero. `restart: unless-stopped` rather than `always` so a container stopped
on purpose during the failure test stays stopped. Limits so one runaway container cannot
starve the host. Full reasoning in [`decisions.md`](decisions.md).

**When should validation fail?**
Whenever the environment stops matching what is documented: a container unhealthy or
paused, an endpoint off-contract, a dependency unready, any instance not serving, nginx
able to reach the datastores, anything but the edge publishing a host port, a prohibited
port open, a container running as root, a missing restart policy or memory limit, PGDATA
not on a named volume, or Redis persistence off. Demonstrated failing twice in
`evidence/14-validate-negative-tests.txt`.

**What does green CI prove, or not prove?**
It proves this commit builds from scratch, starts to a healthy state on a clean Ubuntu
runner with no local cache, serves the whole contract, survives losing a backend,
restores a backup, and keeps data across container recreation. It does **not** prove
correctness under load or concurrency (traffic is one synthetic generator), performance
(a runner is not production hardware), security beyond a non-gating Trivy scan, or that
the four open findings in `security_review.md` have gone away. It also says nothing about
behaviour after the ~90 seconds it runs - a leak or a disk fill would not appear.

**Which single points of failure remain?**
One nginx, one PostgreSQL, one Redis, one host, one Docker daemon, one volume on the same
disk as everything else. Only the app tier is redundant, and that redundancy is worthless
if the host dies. `docker stop postgres` takes `/ready`, `/records` and `/counter` down
for every instance at once. Production: multiple edge instances behind a load balancer,
PostgreSQL with a streaming replica and automated failover, Redis Sentinel or a managed
cache, nodes across availability zones, backups replicated off-host.

**What would you improve?**
In priority order: Redis authentication (`requirepass` plus ACLs) - two lines, needs no
certificate authority, and it is the cheapest real weakness left; TLS at the edge and
`sslmode=verify-full` to PostgreSQL; an `Idempotency-Key` on `POST /records`, which would
make retries unconditionally safe and remove the one trade-off in the proxy config; and
monitoring, without which the silent-data-loss bug in `security_review.md` finding 7 is
exactly the class of problem that stays invisible until someone happens to look.

**How did you verify AI-assisted work?**
Nothing is claimed unless a command was run and its output captured - which is why
`evidence/` exists. Four confident AI-assisted claims were overturned by measurement, and
two regressions introduced by the hardening were caught the same way and are documented
as mine, not as starter faults. Every log number was computed twice by independent means,
and the disagreement between them found two parser bugs. Details in
[`AI_USAGE.md`](AI_USAGE.md).

---

## Repository layout

```
app/            server.py (Flask), healthcheck.py, gunicorn_conf.py
nginx/          nginx.conf
database/       init.sql (unchanged from the starter)
scripts/        analyze_logs.py, make_architecture_diagram.py,
                video_challenge.py (supplied, unchanged)
tests/          app-only contract tests with fake dependencies
logs/           the three supplied historical logs - READ ONLY, unchanged
evidence/       raw captured output backing every claim in the reports
assessment/     the supplied brief and API contract
.github/        the CI workflow
```
