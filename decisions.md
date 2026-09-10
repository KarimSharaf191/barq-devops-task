# Technical decisions

Twelve decisions, each with the alternative I rejected and what it costs me. Where a
choice was settled by a measurement rather than an opinion, the measurement is cited.

Global assumptions, true for all of them:

- This is a **single-host lab** on Docker Compose, bound to `127.0.0.1`, with one
  synthetic client. Nothing here assumes or provides multi-host scheduling.
- The data is disposable synthetic lab data. No real secret, customer record or PII
  exists anywhere in this repository.
- "Production" below means a real deployment with real users, which is explicitly **not**
  what this repository is. Implemented work and production plans are kept apart.

---

## 1. Base image: keep `python:3.12-slim-bookworm`, pinned by digest

- **Choice:** the starter's own digest-pinned `python:3.12-slim-bookworm` for the app;
  digest-pinned `postgres:16-alpine`, `redis:7.4-alpine`, `nginx:1.28-alpine` for the rest.
- **Why:** `slim` gives a Debian/glibc userland, so `psycopg[binary]` installs as a
  prebuilt wheel and nothing needs a compiler in the image. It is roughly a tenth of the
  full `python` image. The **digest** pin is the part that matters most: a tag can be
  moved under you, so `docker build` on a colleague's laptop in three months would
  otherwise produce a different base than the one I tested and validated here.
- **Alternative rejected:** `python:3.12-alpine`. It would swap glibc for musl and force
  a source build of `psycopg`, adding build tools and build time, for a size saving that
  does not matter on a loopback lab. Also rejected: a multi-stage distroless image -
  genuinely smaller and a better production answer, but it removes the shell, which would
  have made every diagnostic step in `troubleshooting.md` impossible to perform live.
- **Trade-off:** a digest pin does not float security patches. It goes stale silently,
  and only a human bumping it fixes that.
- **Evidence / commit:** `2eeca70`.
- **Production improvement:** Renovate or Dependabot raising a PR per digest bump, gated
  on the same CI, plus the Trivy job already in `.github/workflows/ci.yml` promoted from
  reporting to blocking once a triage process exists for it.

## 2. Health checks: `/health` for liveness, `/ready` for readiness, and never mix them

- **Choice:** the container `HEALTHCHECK` and the Compose healthcheck both probe
  `/health`, which touches **no** dependency. `/ready`, which checks PostgreSQL and Redis,
  is used by `validate.py` and by humans - never by Docker.
- **Why:** this distinction is the whole point of having two endpoints. If Docker probed
  `/ready`, a Redis blip would mark two perfectly healthy application containers unhealthy
  and, with a restart policy, restart them - turning a recoverable dependency incident
  into an application outage. The historical logs show exactly this shape: at 11:12 both
  instances returned 503 from `/ready` while the processes themselves were fine
  (`log_analysis.md`, incident 2a).
- **Alternative rejected:** one endpoint doing both. Simpler, and wrong in the one
  situation where it matters.
- **Trade-off:** liveness this shallow will not notice an app that is up but permanently
  broken - a deadlocked worker still answers `/health`.
- **Evidence / commit:** `e3ea094` (the starter probed `/healthz`, which does not exist,
  so both apps were permanently unhealthy), `2eeca70` (`app/healthcheck.py`).
- **Production improvement:** Kubernetes-style split - `/health` as liveness, `/ready` as
  a readiness gate that removes the pod from the load balancer without restarting it.

## 3. Health-check tooling: the interpreter already in the image, not curl

- **Choice:** the app probes with `python -m app.healthcheck`; nginx probes with the
  busybox `wget` already inside `nginx:alpine`.
- **Why:** the brief asks for health-check tools installed in the image. Both images
  already contain a capable one. Adding `curl` to `python:slim` means an `apt-get` layer,
  a larger image and more attack surface, to do something the interpreter does natively.
  A module also does more than `curl -f`: it asserts HTTP 200 **and** `status == "alive"`,
  and prints why it failed, so `docker inspect` shows a diagnosis rather than an exit code.
- **Alternative rejected:** `HEALTHCHECK CMD curl -f http://127.0.0.1:8080/health`.
  Familiar, one line, but it needs a package that is not there and it cannot tell the
  difference between "answered 200" and "answered 200 with the wrong body".
- **Trade-off:** a Python interpreter start per probe is heavier than `curl` - roughly
  30ms every 5s per container. Irrelevant here, worth revisiting at high container density.
- **Evidence / commit:** `2eeca70`; `docker inspect` output in
  `evidence/12-stageF-hardening.txt`.

## 4. Networks: two networks, `backend` internal, and NGINX on `frontend` only

- **Choice:** `frontend` (nginx + apps) and `backend` (apps + postgres + redis, with
  `internal: true`). The edge proxy is not attached to `backend`.
- **Why:** nginx is the only container reachable from the host, so it is the one most
  likely to be attacked. It has no business reaching the database. Removing it from
  `backend` means a compromised proxy cannot open a socket to PostgreSQL at all -
  `getent hosts postgres` inside the container returns exit 2, the name does not resolve.
  `internal: true` additionally denies the datastores outbound internet access, which is
  the cheapest possible mitigation against a compromised database exfiltrating data.
- **Alternative rejected:** one flat network. Simpler and it is what the starter almost
  had. It makes every container one `nc` away from the database.
- **Trade-off:** the apps straddle both networks, so they remain the pivot point. Two
  networks reduce blast radius, they do not eliminate it.
- **Evidence / commit:** `fb9012a`; `validate.py` asserts it both ways - by topology and
  by proving nginx cannot resolve the names at runtime (`evidence/10-stageD-isolation.txt`).
- **Production improvement:** network policy at the orchestrator level plus mTLS between
  tiers, so the boundary does not depend on the docker0 bridge.

## 5. Load balancing: shared-memory round robin, not per-worker

- **Choice:** `zone application_pool 64k;` in the upstream block.
- **Why:** **measured, not assumed.** Without it, each of the 16 nginx workers keeps a
  private round-robin cursor, so the first request each cold worker handles goes to the
  same peer. Twelve consecutive requests after a reload all landed on `app-01`,
  reproducibly. With the zone, the same test splits 6/6.
- **Alternative rejected:** `least_conn`, which is better when request cost varies. Here
  every request is cheap and uniform, and `least_conn` would have hidden the per-worker
  bug rather than fixing it.
- **Trade-off:** round robin distributes *requests*, not *load*. The moment endpoint cost
  diverges, this is the wrong policy.
- **Evidence / commit:** `add6a06`; before/after in
  `evidence/08-nginx-worker-rr-anomaly.txt` and `evidence/11-stageE-availability.txt`;
  `troubleshooting.md` entry 13.

## 6. Retries: retry on connect failures, never retry a request already sent

- **Choice:** `proxy_next_upstream error timeout http_502 http_503 http_504;`
  `proxy_next_upstream_tries 2;` `proxy_next_upstream_timeout 12s;`
  `max_fails=3 fail_timeout=5s`. `non_idempotent` is deliberately **not** set.
- **Why:** nginx withholds a retry for a non-idempotent method only once the request has
  actually been **sent** upstream. A refused connect happens before anything is sent, so
  a `POST /records` is still retried - safely, because the backend provably never saw it.
  If the request was sent and then timed out, the write may have committed and only the
  answer was lost; retrying there could insert the row twice. This gives the strongest
  guarantee available without an idempotency key: retry exactly when we know nothing
  happened.
- **I got this wrong first.** I predicted POSTs would fail during the outage. They did
  not - 54/54 succeeded. Measuring it corrected my model of the directive
  (`troubleshooting.md` entry 16).
- **Alternative rejected:** `non_idempotent` (duplicate writes on timeout) and
  `proxy_next_upstream off`, the starter's value, which returns every backend failure
  straight to the client.
- **Trade-off:** a `POST` that times out mid-flight still fails for the user. That is the
  correct failure - a duplicate record is worse than a 504 the client can retry knowingly.
- **Evidence / commit:** `add6a06`, `df42b54`; `evidence/15-failure-test.txt` shows
  100.00% GET and POST availability with a backend stopped.
- **Production improvement:** an `Idempotency-Key` header persisted with the row, which
  makes retries safe unconditionally and removes the trade-off entirely.

## 7. Timeouts: `proxy_read_timeout` 5s, retry budget 12s - and why they must differ

- **Choice:** `proxy_connect_timeout 2s`, `proxy_send_timeout 5s`, `proxy_read_timeout 5s`,
  `proxy_next_upstream_timeout 12s`, gunicorn `timeout 30`, app dependency timeouts 2s.
- **Why:** they form a ladder, each layer more patient than the one below it. 2s connect
  matches the app's own 2s dependency connect timeout. 5s read is above the worst
  legitimate response - `/ready` with an unreachable PostgreSQL takes ~4s - so the proxy
  never replaces the app's informative 503 with an opaque 504. gunicorn's 30s is far
  above all of it, so a worker is killed only for a genuine hang.
- **The 12s is the one I got wrong first.** I originally set
  `proxy_next_upstream_timeout` to 5s, the same as `proxy_read_timeout`. A hung backend
  then consumed the entire retry budget before a retry could start: a paused backend
  produced three client-visible 504s at ~4.88s each. Raising the budget to 12s produced
  zero 504s in the identical test. Two timeouts that each looked reasonable were mutually
  exclusive, and only measurement showed it.
- **Alternative rejected:** aggressive 1-2s read timeouts. Faster failure detection, at
  the cost of converting every legitimately slow dependency check into a 504 and throwing
  away the diagnostic value of the app's own 503.
- **Trade-off:** with `max_fails=3`, the first three requests to a newly hung backend are
  still slow (~4.9s) before it is ejected. Open-source nginx has no active health checks,
  so some client-visible latency during detection is unavoidable.
- **Evidence / commit:** `df42b54`; before/after in
  `evidence/16-paused-backend-behaviour.txt`; `troubleshooting.md` entry 17.
- **Production improvement:** a load balancer with **active** health probes, which ejects
  a peer before any client request hits it.

## 8. Restart policy and resource limits

- **Choice:** `restart: unless-stopped` everywhere (the starter had `restart: "no"`);
  per-service CPU and memory limits - app 0.5 CPU / 256M, postgres 1.0 / 512M,
  redis 0.5 / 192M, nginx 0.5 / 128M - plus memory reservations and
  `stop_grace_period` tuned per service (postgres 30s, redis 15s, app and nginx 10s).
- **Why:** `unless-stopped` rather than `always` so that a container I deliberately stop
  during the failure test **stays** stopped - `always` would restart it under me and make
  the test meaningless. Limits exist so one runaway container cannot starve the host; the
  numbers come from observed usage with headroom, not from a formula. The grace periods
  are ordered by how much each service loses on an abrupt kill: PostgreSQL gets the most
  time to complete a clean shutdown, nginx the least because it is stateless.
- **Alternative rejected:** no limits (the starter's state), which is fine until it is
  not; and `restart: always`, which fights the operator.
- **Trade-off:** a memory limit turns a leak into an OOM kill instead of a slow host.
  That is the better failure, but it is still a failure, and 256M is a guess informed by
  one workload rather than by a load test.
- **Evidence / commit:** `add6a06`; `validate.py` asserts a restart policy and a non-zero
  memory limit for every service.
- **Production improvement:** limits derived from real percentile usage, plus alerting on
  OOM kills and on sustained throttling, which a limit alone will not tell you about.

## 9. Storage: PGDATA on a named volume; Redis AOF every second

- **Choice:** `postgres-data` mounted at `/var/lib/postgresql/data` with
  `PGDATA=/var/lib/postgresql/data/pgdata`; Redis with `appendonly yes`,
  `appendfsync everysec`, an RDB snapshot as a second copy, and
  `maxmemory-policy noeviction`.
- **Why:** the starter mounted the named volume at `/var/lib/postgresql/backup`, a path
  PostgreSQL never writes to, and put the real PGDATA on a `tmpfs` - so the stack reported
  healthy while silently discarding every row. `PGDATA` points at a **sub-directory** of
  the mount because a volume root can carry `lost+found`, which makes `initdb` refuse to
  start. `noeviction` matters more than it looks: the Redis counter is *state*, not cache,
  and the default eviction policy would treat it as disposable under memory pressure.
- **Alternative rejected:** `appendfsync always` (durable to the write, materially
  slower) and `appendonly no` with RDB only (up to 15 minutes of loss).
- **Trade-off:** `everysec` can lose up to one second of counter increments on an unclean
  stop. For a request counter that is the right trade; for money it would not be.
- **Evidence / commit:** `432f4e2`; `evidence/09-stageC-persistence.txt` shows record
  `id=3` surviving `docker compose rm -sf app-01 app-02 postgres` followed by `up -d`.
- **Production improvement:** PITR with continuous WAL archiving and a restore rehearsed
  on a schedule, plus off-host storage - a named volume on one machine is not a backup.

## 10. Secrets: one copy, in a git-ignored `.env`, never in the image

- **Choice:** delete `config/app.env`; supply `POSTGRES_*` once from a git-ignored `.env`
  and interpolate that single value into both the postgres service and `DATABASE_URL`;
  ship `.env.example` with a placeholder; drop the `COPY config/app.env` from the
  Dockerfile; redact credentials in application logs; regenerate the lab password.
- **Why:** the starter held the password in **two** places that had silently drifted
  apart by one character (`...8c` vs `...8d`), which was half of the `/ready` failure.
  Deduplicating the value does not just tidy it - it makes that class of bug impossible.
  The credential was also baked into an image layer (surviving `docker history` and any
  registry push) and printed in clear text at every startup.
- **Alternative rejected:** Docker secrets / `_FILE` env conventions. Better, and the
  right production answer, but on single-host Compose without Swarm they land as bind
  mounts, which is more moving parts for the same practical result at lab scale.
- **Trade-off:** `.env` is plaintext on disk, readable by anything running as this user.
  It is a lab-appropriate control, not a real secrets solution.
- **Limitation stated plainly:** the **baseline commit** still contains the original
  `BarqLabOnly_...` password, because the brief requires keeping the starter history
  intact. It is synthetic lab data, and it is now dead - the password was regenerated, so
  the committed value opens nothing.
- **Evidence / commit:** `ea1d99b`; `evidence/07-stageB-verify.txt` shows
  `/srv/app.env: No such file or directory` in the image and `barq_app:***@` in the log.
- **Production improvement:** a real secret manager (Vault, AWS/GCP secret manager) with
  short-lived, automatically rotated database credentials.

## 11. Run under gunicorn as a non-root user with a read-only root filesystem

- **Choice:** `USER app:app` (uid 10001), `read_only: true` with a 32M tmpfs at `/tmp`,
  `cap_drop: ALL`, `no-new-privileges`, and gunicorn with gthread workers instead of the
  Flask development server. nginx likewise runs as uid 101 with a read-only root, which
  is why it listens on 8080 in-container - an unprivileged process cannot bind port 80.
- **Why:** the starter created an unprivileged account and then discarded it with a
  trailing `USER root`. `gunicorn` was pinned in `requirements.txt` but never invoked, so
  the dependency was dishonest; using it makes the pin real and removes a development
  server from a proxied deployment. Endpoint semantics are untouched - it is the same
  WSGI callable.
- **Alternative rejected:** keeping the Flask server (single-process, warns at startup,
  not built to face a proxy) and running as root (nothing needed it).
- **Trade-off:** hardening has a cost, and I paid it: `read_only` plus a home-less user
  made gunicorn 26 log `Control server error: [Errno 30] Read-only file system:
  '/home/app'` on every boot. I disabled the control socket rather than granting a
  writable home for a feature this deployment never uses - but a non-fatal `[ERROR]` on
  every start is exactly how operators learn to ignore error logs
  (`troubleshooting.md` entry 15).
- **Evidence / commit:** `2eeca70`; `evidence/12-stageF-hardening.txt` shows
  `uid=10001(app)`, writes to `/srv` refused, and `grep -c ERROR` returning 0.

## 12. Validation discovers services instead of hard-coding names

- **Choice:** `validate.py`, `failure_test.py`, `backup.sh` and `restore.sh` all resolve
  containers through `docker compose -p <project> ps`, never by bare container name.
- **Why:** two reasons, one safety and one function. Safety: `scripts/README.md` warns
  against targeting an unrelated project, and container names like `postgres` and `nginx`
  are exactly the names another project on the same machine is likely to be using -
  `docker stop postgres` could hit someone else's database. Function: the video requires
  adding a third instance live, and discovery means `app-03` is validated automatically
  with no edit to the validator.
- **Alternative rejected:** hard-coded names. Shorter, and it would have needed editing
  on camera at the worst possible moment.
- **Trade-off:** discovery makes the scripts depend on Compose labels and on services
  being named `app-*`. A renamed service silently drops out of the checks.
- **Evidence / commit:** `df42b54`, `2a87ccf`; `validate.py` reports
  `expected-services-present` with the discovered list so a silent drop-out is visible.
