# Security and production-readiness review

Fourteen findings against **my final solution**, not against the starter. Findings 1-10
describe risks I found and closed; findings 11-14 are risks that **remain open** in what
I am submitting. Every finding names how to verify it, so nothing here has to be taken
on trust.

Scope note: this is a single-host lab bound to `127.0.0.1` with synthetic data. Several
findings would be rated far higher in a real deployment, and the ratings below say which.

---

## Part A - Implemented in this repository

### 1. Credentials were baked into the image and committed to Git

- **Risk and evidence:** the Dockerfile ran `COPY config/app.env /srv/app.env`, and
  `config/app.env` was tracked in Git. The credential therefore existed in an image
  layer, in `docker history`, in anything pulled from a registry, and in the repository
  for anyone with read access.
  ```
  $ docker run --rm --entrypoint sh barq-assessment-app-01:latest -c "cat /srv/app.env"
  DATABASE_URL=postgresql://barq_app:BarqLabOnly_7qN2vK8d@postgres:5433/barq_tasks
  ```
- **Impact:** full database credential disclosure to anyone who can pull the image. In
  production this is a direct path to all customer data. **Critical.**
- **Implemented fix:** removed the `COPY`, deleted `config/app.env` from the repository,
  moved every secret to a git-ignored `.env` with a placeholder-only `.env.example`, and
  added `config/app.env`, `.env` and `.env.*` to both `.gitignore` and `.dockerignore`.
  The lab password was regenerated, so the value in the baseline commit is dead.
  Commit `ea1d99b`.
- **Production follow-up:** a real secret manager issuing short-lived, rotated database
  credentials; `gitleaks` or `trufflehog` as a pre-commit hook and a CI gate; and
  history rewriting or credential rotation for anything already committed.
- **How to verify:**
  ```bash
  docker run --rm --entrypoint sh barq-assessment-app:latest -c 'ls /srv'   # app, requirements.txt
  git ls-files | grep -c 'config/app.env'                                    # 0
  docker image inspect barq-assessment-app:latest --format '{{json .Config.Env}}' | grep -c PASSWORD  # 0
  ```
- **Deliberate remaining occurrence, declared rather than hidden:** a scan of tracked
  files still finds the old `BarqLabOnly_...` string twice, in
  `evidence/04-baseline-deepdive.txt`. That file *is* the proof of this finding - it is
  the captured `docker run ... cat /srv/app.env` and the startup log showing the leak.
  Redacting it would destroy the evidence for the fault it documents. The value is
  synthetic lab data, it is already in the supplied baseline commit which the brief
  requires keeping, and it was regenerated, so it opens nothing. Verify with:
  ```bash
  git grep -lI 'BarqLabOnly' -- . ':!logs' ':!assessment'   # evidence/ and docs only
  ```

### 2. The same secret existed in two places and had drifted

- **Risk and evidence:** the password in `config/app.env` ended `...7qN2vK8d`; the one in
  `docker-compose.yml` ended `...7qN2vK8c`. One character apart, and it was half the
  reason `/ready` returned 503.
- **Impact:** beyond the outage, duplicated secrets guarantee rotation failures - you
  rotate one copy, the other keeps working until it silently does not. **High.**
- **Implemented fix:** exactly one copy, `POSTGRES_PASSWORD` in `.env`, interpolated into
  both the postgres service and the app's `DATABASE_URL`. The two can no longer disagree
  because there is no longer a second value. Commit `ea1d99b`.
- **Production follow-up:** the same principle enforced by construction - the application
  reads the credential from the secret store, not from a copy.
- **How to verify:** `grep -c 'POSTGRES_PASSWORD' docker-compose.yml` is 1, and that one
  occurrence is a `${...}` reference, not a literal.

### 3. Containers ran as root

- **Risk and evidence:** `docker exec app-01 id` returned `uid=0(root)`. The Dockerfile
  created an unprivileged `app` user and then discarded it with a trailing `USER root`.
- **Impact:** any RCE in the Flask app started as root inside the container, which
  removes the first barrier to a container escape and makes every kernel-level escape
  primitive available. **High.**
- **Implemented fix:** `USER app:app` (uid 10001), plus `cap_drop: ALL`,
  `no-new-privileges:true`, `read_only: true` and a size-capped tmpfs at `/tmp`. nginx
  likewise runs as uid 101 with a read-only root, listening on 8080 in-container because
  an unprivileged process cannot bind port 80. Commit `2eeca70`.
- **Production follow-up:** a seccomp and AppArmor profile, user-namespace remapping so
  container uid 10001 is not a real host uid, and rootless Docker or Podman.
- **How to verify:** `python validate.py` runs `runs-as-non-root[...]` for every app and
  the edge; `docker exec app-01 touch /srv/x` must fail with `Read-only file system`.

### 4. The edge proxy could reach the datastores directly

- **Risk and evidence:** nginx was attached to the `backend` network.
  `docker exec nginx getent hosts postgres redis` resolved both.
- **Impact:** the one container reachable from outside was one TCP connection away from
  PostgreSQL and Redis. A proxy compromise - the most likely single compromise in this
  design - would have reached the database without touching the application. **High.**
- **Implemented fix:** nginx is on `frontend` only; `backend` is `internal: true`, which
  also denies the datastores outbound internet access and so raises the cost of
  exfiltration from a compromised database. Commit `fb9012a`.
- **Production follow-up:** orchestrator network policy plus mTLS between tiers, so the
  boundary does not depend on a bridge network.
- **How to verify:** `validate.py` checks it twice - by topology and by proving at runtime
  that nginx cannot resolve either name (`isolation-edge-cannot-resolve[...]`).

### 5. Datastore host ports were declared

- **Risk and evidence:** `postgres` and `redis` declared `127.0.0.1:15432:5432` and
  `127.0.0.1:16379:6379`.
- **Impact:** **Low as it actually stood, and I want to be precise about why.** Docker
  cannot publish a host port for a container attached only to an `internal` network, so
  the publish silently did nothing - `netstat` and `docker port` both confirmed nothing
  was listening. The real risk was latent: attaching postgres to any non-internal network
  later would have exposed an unauthenticated-from-localhost database with no further
  edit, and `127.0.0.1` binding is no protection on a machine with any other local user
  or a port-forwarding tunnel.
- **Implemented fix:** both `ports:` blocks removed. nginx is the only publisher. Commit
  `fb9012a`.
- **Production follow-up:** database access through a bastion or an authenticated proxy
  with audit logging; never a published port.
- **How to verify:** `validate.py` asserts `only-edge-publishes-a-host-port` and that
  127.0.0.1:5432, :6379, :15432 and :16379 all refuse connections.

### 6. The application logged its own database password on every start

- **Risk and evidence:**
  ```
  {"event":"configuration_loaded",
   "database_url":"postgresql://barq_app:BarqLabOnly_7qN2vK8d@postgres:5433/barq_tasks"}
  ```
- **Impact:** secrets in logs spread further than secrets anywhere else - they reach log
  aggregators, backups, screenshots and support tickets, and they survive rotation of the
  original store. **High.**
- **Implemented fix:** `redact_url()` in `app/server.py`, applied before logging, with
  three unit tests including a password that itself contains an `@`. The log now reads
  `postgresql://barq_app:***@postgres:5432/barq_tasks`. Commits `ea1d99b`, `2eeca70`.
- **Production follow-up:** redaction in the logging pipeline as well as at the call site
  (defence in depth), and a CI check that fails on credential-shaped strings in captured
  log output.
- **How to verify:**
  ```bash
  docker logs app-01 | grep configuration_loaded    # shows barq_app:***@
  python -m unittest tests.test_app.RedactionTests -v
  ```

### 7. No persistence: the database silently discarded everything

- **Risk and evidence:** the named volume was mounted at `/var/lib/postgresql/backup`,
  which PostgreSQL never writes to, while the real PGDATA was a `tmpfs`:
  ```
  $ docker exec postgres df -h /var/lib/postgresql/data
  tmpfs  3.8G  45.9M  3.8G  1%  /var/lib/postgresql/data
  ```
- **Impact:** total data loss on any container recreation, with **no error and a green
  health check**. Silent data loss is worse than loud data loss: backups would have
  succeeded, monitoring would have stayed green, and the loss would only surface when
  someone looked for an old record. **Critical.**
- **Implemented fix:** volume mounted at the real PGDATA with `PGDATA` set to a
  sub-directory; `tmpfs` removed; Redis given AOF plus a named volume and
  `maxmemory-policy noeviction` so the shared counter is never evicted as if it were
  cache. Commit `432f4e2`.
- **Production follow-up:** PITR with continuous WAL archiving to off-host storage, and a
  restore rehearsed on a schedule rather than assumed.
- **How to verify:** `validate.py` checks `postgres-pgdata-on-named-volume` and
  `postgres-pgdata-not-tmpfs`; end-to-end proof in `evidence/09-stageC-persistence.txt`.

### 8. Backups that were never proved to restore

- **Risk and evidence:** `backup.sh` and `restore.sh` were unimplemented placeholders
  exiting 2. A dump nobody has read back is not a backup.
- **Impact:** discovering a corrupt or empty dump during an incident. **High.**
- **Implemented fix:** `backup.sh` verifies every dump by reading its table of contents
  with `pg_restore --list` before reporting success. The restore is proved by *changing
  the data first*: create a row, dump, insert a second row and delete an old one,
  restore, then confirm the post-dump row is gone and the deleted row is back. CI runs
  the same round trip and fails the build if the post-dump row survives. Commits
  `2a87ccf`, `966dd6d`.
- **Production follow-up:** off-host encrypted storage, retention and integrity
  monitoring, and an automated restore drill into a scratch database on a schedule.
- **How to verify:** `./backup.sh && FORCE=1 ./restore.sh`; full transcript in
  `evidence/17-backup-restore-proof.txt`.

### 9. No failover, so one backend loss was a client-visible outage

- **Risk and evidence:** `proxy_next_upstream off` with `max_fails=0`, and
  `restart: "no"` on the apps. The historical logs show the cost of exactly this: at
  11:05 only 19 of 59 upstream connect failures were masked by a retry; 40 became
  client-visible 502s.
- **Impact:** availability, and availability is a security property - a service that is
  down cannot enforce anything, and outages are what pressure people into unsafe
  workarounds. **Medium.**
- **Implemented fix:** `proxy_next_upstream error timeout http_502 http_503 http_504` with
  a retry budget above the read timeout, `max_fails=3 fail_timeout=5s`, a shared
  round-robin zone, and `restart: unless-stopped`. Measured: 100.00% GET **and** POST
  availability with a backend stopped. Commits `add6a06`, `df42b54`.
- **Production follow-up:** a load balancer with active health probes, and more than one
  host.
- **How to verify:** `python failure_test.py` (`evidence/15-failure-test.txt`).

### 10. Unbounded container logs and unbounded resources

- **Risk and evidence:** no `logging` options and no `deploy.resources.limits` on any
  service in the starter.
- **Impact:** two denial-of-service paths that need no attacker sophistication at all. A
  chatty or looping container fills the host disk through `json-file` logs, which takes
  down every other container on the box including the database. A memory leak or a
  request flood starves every neighbour. **Medium.**
- **Implemented fix:** `json-file` with `max-size: 10m, max-file: 3` on every service
  (bounded at 30M each, ~150M total), and CPU/memory limits and reservations per service.
  Commit `add6a06`.
- **Production follow-up:** ship logs off-host so rotation is not the retention policy,
  and alert on disk pressure, OOM kills and sustained CPU throttling - a limit tells you
  nothing by itself.
- **How to verify:** `validate.py` asserts a non-zero memory limit for every service;
  `docker inspect app-01 --format '{{json .HostConfig.LogConfig}}'`.

---

## Part B - Risks that remain OPEN in what I am submitting

These are not fixed. They are listed because a review that only lists solved problems is
a marketing document.

### 11. Every request crosses the network in plaintext HTTP

- **Risk:** nginx serves plain HTTP on `127.0.0.1:8080`. The app-to-PostgreSQL and
  app-to-Redis connections are also unencrypted, and Redis has **no authentication at
  all** - no `requirepass`, no ACL. Anything with a foothold on the `backend` network can
  read and write the cache freely.
- **Impact:** on loopback with synthetic data this is genuinely low. On any real network
  it is **Critical**: credentials and record contents in clear text, and a completely open
  cache.
- **Why not fixed:** the brief specifies HTTP on a local port, and terminating TLS would
  have meant either a self-signed certificate that every `curl` in the evidence needs
  `-k` for, or a certificate authority this lab has no way to reach. Adding Redis
  authentication would have been cheap and I consider it the weakest item on this list.
- **Production plan:** TLS at the edge with certificates from an ACME provider, HSTS,
  `sslmode=verify-full` on the PostgreSQL connection, and Redis with `requirepass` plus
  ACLs restricting the app user to the keys it actually needs.
- **How to verify it is still open:** `docker exec redis redis-cli ping` succeeds with no
  credential; `curl -v http://127.0.0.1:8080/` shows no TLS.

### 12. The application has no authentication, authorisation or rate limiting

- **Risk:** `POST /records` is unauthenticated. Anyone who can reach the port can write
  rows until the disk fills. There is no per-client rate limit anywhere -
  `limit_req_zone` is not configured in nginx.
- **Impact:** **High** for any deployment that is not loopback-only. The 200-character
  title cap and the 64k body limit bound a single request, not the request *rate*.
- **Why not fixed:** the supplied API contract in `assessment/APPLICATION.md` defines
  these endpoints as unauthenticated, and I was told not to change endpoint semantics.
  Adding auth would have broken the contract the assessment validates against.
- **Production plan:** authentication at the edge (OAuth2/OIDC or mTLS), per-principal
  authorisation, `limit_req` with a burst allowance, and a body-size limit tuned per route.
- **How to verify it is still open:** `curl -d '{"title":"x"}' http://127.0.0.1:8080/records`
  returns 201 with no credential of any kind.

### 13. Single point of failure everywhere below the app tier

- **Risk:** exactly one nginx, one PostgreSQL, one Redis, one host, one Docker daemon,
  one named volume. Only the app tier is redundant, and even that redundancy is
  meaningless if the host dies. The volume is on the same disk as everything else, so a
  disk failure loses the data *and* the backups unless they were copied off-host.
- **Impact:** **High** for availability and for durability.
- **Why not fixed:** genuinely out of scope for single-host Compose. Pretending otherwise
  would be the "material contradiction" the brief warns about.
- **Production plan:** two or more edge instances behind a virtual IP or cloud load
  balancer; PostgreSQL with a streaming replica and automated failover; Redis Sentinel or
  a managed cache; nodes spread across availability zones; backups replicated off-host
  with their own retention.
- **How to verify it is still open:** `docker stop postgres` takes `/ready`, `/records`
  and `/counter` down for every instance simultaneously - the app tier's redundancy does
  not help at all.

### 14. No monitoring, alerting or audit trail

- **Risk:** structured JSON logs go to `docker logs` and nowhere else. Nothing scrapes
  them, nothing alerts on them, and they are deleted by rotation after ~30M per service.
  There are no metrics, no traces and no audit log of who changed what. `validate.py` and
  `failure_test.py` are run by a human, not on a schedule.
- **Impact:** **High**, and it is the finding that makes the others worse. An intrusion
  or a slow data-loss bug would be invisible until someone happened to look - which is
  precisely the failure mode of the persistence bug in finding 7, and precisely what made
  the historical incident in `log_analysis.md` analysable only because someone kept the
  logs by hand.
- **Why not fixed:** out of scope for the brief, and a monitoring stack would have
  dominated a single-host lab.
- **Production plan:** ship logs to a central store with retention independent of the
  host; Prometheus metrics from nginx and the app with alerts on 5xx rate, p95 latency,
  restart count, OOM kills, replication lag and backup age; distributed tracing keyed on
  the `X-Request-ID` the app already emits; and CI scheduled nightly, not only on push.
- **How to verify it is still open:** stop `app-01` and nothing anywhere raises an alert;
  `docker logs` is the only record, and it is capped at three 10M files.

---

## Summary

| # | Finding | Severity (this lab / production) | Status |
|---|---|---|---|
| 1 | Credentials in the image and in Git | Medium / **Critical** | Fixed `ea1d99b` |
| 2 | Duplicated, drifted secret | Medium / High | Fixed `ea1d99b` |
| 3 | Containers running as root | Medium / High | Fixed `2eeca70` |
| 4 | Edge proxy could reach the datastores | Medium / High | Fixed `fb9012a` |
| 5 | Datastore host ports declared | Low / High | Fixed `fb9012a` |
| 6 | Password written to the application log | Medium / High | Fixed `ea1d99b` |
| 7 | Silent total data loss | **Critical** / **Critical** | Fixed `432f4e2` |
| 8 | Unproved backups | High / High | Fixed `2a87ccf` |
| 9 | No failover | Medium / Medium | Fixed `add6a06` |
| 10 | Unbounded logs and resources | Medium / Medium | Fixed `add6a06` |
| 11 | Plaintext HTTP; unauthenticated Redis | Low / **Critical** | **Open** |
| 12 | No authn/authz or rate limiting | Low / High | **Open** |
| 13 | Single point of failure below the app tier | High / High | **Open** |
| 14 | No monitoring, alerting or audit trail | High / High | **Open** |

If I could fix only one open item within the lab's constraints it would be **11**,
specifically Redis authentication: it is a two-line change, it needs no certificate
authority, and an unauthenticated datastore on a shared network is the cheapest real
weakness left in the design.
