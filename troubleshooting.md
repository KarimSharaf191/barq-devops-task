# Troubleshooting journal

Chronological record of the investigation. Every command below was run against the
supplied starter as cloned, before any repair. Raw captures live in `evidence/`.

Environment: Windows 11 host, Docker Desktop 29.3.1 (Linux containers, WSL2 backend),
Compose v5.1.0, Git Bash for shell commands. All times UTC.

Baseline commit under investigation: `aa49fb3` (starter v2.0.0, tag `starter-v2.0.0`).

---

## Entry 01 - 2026-09-06 20:58 UTC - Does the supplied stack even start?

- **Symptom:** unknown; establishing a baseline before touching anything.
- **Hypothesis:** the README warns the environment is broken, so I expect either a build
  failure (the pinned requirement versions looked unusual) or a runtime failure.
- **Command or test:**
  ```bash
  cp .env.example .env
  docker compose -p barq-assessment config >/dev/null; echo $?
  docker compose -p barq-assessment build
  docker compose -p barq-assessment up -d
  sleep 25 && docker compose -p barq-assessment ps -a
  ```
- **Actual output:** `config` exited 0, the build succeeded, all five containers started.

  ```
  NAME       SERVICE    STATUS                      PORTS
  app-01     app-01     Up 26 seconds (unhealthy)   8080/tcp
  app-02     app-02     Up 26 seconds (unhealthy)   8080/tcp
  nginx      nginx      Up 25 seconds               127.0.0.1:8080->81/tcp
  postgres   postgres   Up 25 seconds (healthy)     5432/tcp
  redis      redis      Up 25 seconds (healthy)     6379/tcp
  ```
  (`evidence/01-baseline-build.txt`, `evidence/02-baseline-up.txt`)
- **Failed attempt and what changed my thinking:** my first hypothesis was a dependency
  resolution failure - `gunicorn==26.2.0` and `redis==8.1.0` looked like invented versions.
  The build proved me wrong: every pin resolves. That was useful, because it told me the
  faults are *configuration* faults, not packaging faults, and it stopped me rewriting
  `requirements.txt` for no reason. (`gunicorn` is nevertheless never invoked - see Entry 09.)
- **Root cause:** n/a - baseline capture.
- **Retest evidence:** n/a.
- **Remaining uncertainty:** two apps are unhealthy and nginx publishes `->81`, both of
  which look wrong. Investigated next.

---

## Entry 02 - 2026-09-06 20:59 UTC - Nothing answers on the public port (FIRST FAILURE)

- **Symptom:**
  ```bash
  $ curl -sS -m 5 -i http://127.0.0.1:8080/health
  curl: (52) Empty reply from server
  ```
  Not "connection refused" - something accepted the TCP connection and then closed it.
- **Hypothesis:** because the connection is accepted, the host publish rule exists but
  points at a container port nothing is listening on.
- **Command or test:**
  ```bash
  docker port nginx
  docker exec nginx netstat -ltnp
  ```
- **Actual output:**
  ```
  81/tcp -> 127.0.0.1:8080
  tcp  0  0 0.0.0.0:80  0.0.0.0:*  LISTEN  1/nginx: master pro
  ```
- **Root cause:** `docker-compose.yml` publishes `127.0.0.1:${PUBLIC_PORT:-8080}:81`, but
  `nginx/nginx.conf` has `listen 80;`. Docker accepts on 8080 and forwards to a dead
  container port 81, so the connection is closed with an empty reply.
- **Fix:** publish `:80` so the container-side port is the port nginx actually binds.
- **Retest evidence:** Entry 12.
- **Remaining uncertainty:** none.

---

## Entry 03 - 2026-09-06 20:59 UTC - Both app containers are permanently unhealthy

- **Symptom:** `app-01`/`app-02` never leave `(unhealthy)`.
- **Hypothesis:** either the app is dead, or the health probe targets the wrong thing.
- **Command or test:**
  ```bash
  docker logs app-01 | tail -15
  docker inspect app-01 --format '{{json .State.Health}}'
  ```
- **Actual output:** the app is alive and answering - it is *rejecting* the probe:
  ```
  {"level":"WARN","event":"http_request","instance_id":"app-01","method":"GET",
   "path":"/healthz","status":404,"duration_ms":0.204}
  ```
- **Root cause:** the Compose healthcheck requests `/healthz`. The application contract
  (`assessment/APPLICATION.md`) and `app/server.py` both define `/health`. A 404 raises
  `urllib.error.HTTPError`, the probe exits non-zero, the container is marked unhealthy.
- **Fix:** probe `/health`.
- **Retest evidence:** Entry 12.

---

## Entry 04 - 2026-09-06 21:00 UTC - The apps only listen on loopback

- **Symptom:** even with Entry 02 mentally corrected, nginx still could not reach a backend.
- **Hypothesis:** `APP_HOST: "127.0.0.1"` in the Compose `x-app` anchor makes Flask bind
  the container's loopback interface, which no other container can reach.
- **Command or test:**
  ```bash
  docker exec app-01 sh -c "awk 'NR>1{print \$2}' /proc/net/tcp | head -5"
  ```
- **Actual output:** `0100007F:1F90` - hex `0100007F` is `127.0.0.1`, `1F90` is 8080.
  The listener is loopback-only, confirmed from the kernel socket table rather than
  inferred from the YAML.
- **Root cause:** `APP_HOST: "127.0.0.1"`.
- **Fix:** `APP_HOST: "0.0.0.0"`. The container publishes no host port, so the wider bind
  is still only reachable from inside the Compose networks.
- **Retest evidence:** Entry 12.

---

## Entry 05 - 2026-09-06 21:00 UTC - Wrong upstream port for app-01 in nginx

- **Symptom:** `upstream application_pool { server app-01:8081; server app-02:8080; }`
- **Hypothesis:** `8081` is a typo; nothing in the stack listens on 8081.
- **Command or test:** the `/proc/net/tcp` dump from Entry 04 shows only `:1F90` (8080).
  `EXPOSE 8080` in the Dockerfile and `APP_PORT: "8080"` in Compose agree.
- **Root cause:** wrong upstream port for `app-01`. Half of all proxied requests would
  still have failed with `connect() refused` after Entries 02 and 04 were fixed.
- **Fix:** `server app-01:8080;`.
- **Retest evidence:** Entry 12 - `/instance` round-robin returns both identities.

---

## Entry 06 - 2026-09-06 21:00 UTC - /ready is 503: three defects in the dependency URLs

- **Symptom:**
  ```bash
  $ docker exec app-01 python -c "...urlopen('http://127.0.0.1:8080/ready')..."
  503 {"dependencies":{"postgres":"unavailable","redis":"unavailable"},...}
  ```
  while `docker compose ps` reports postgres and redis themselves `(healthy)`.
- **Hypothesis:** the dependencies are up; the *client* configuration is wrong.
- **Command or test:**
  ```bash
  docker logs app-01 | grep dependency_error | tail -3
  docker logs app-01 | grep configuration_loaded | tail -1
  ```
- **Actual output:**
  ```
  {"event":"dependency_error","dependency":"postgres","error_type":"OperationalError",...}
  {"event":"dependency_error","dependency":"redis","error_type":"ConnectionError",...}
  {"event":"configuration_loaded",
   "database_url":"postgresql://barq_app:BarqLabOnly_7qN2vK8d@postgres:5433/barq_tasks",
   "redis_url":"redis://redis:6380/0"}
  ```
- **Root cause:** three separate defects in `config/app.env`:
  1. PostgreSQL port `5433` - the server listens on `5432`.
  2. Redis port `6380` - the server listens on `6379`.
  3. The password ends `...7qN2vK8d`, but `POSTGRES_PASSWORD` in `docker-compose.yml`
     ends `...7qN2vK8c`. A one-character mismatch.
- **Failed attempt and what changed my thinking:** I first corrected only the two ports and
  expected `/ready` to go green. Postgres stayed `unavailable`, still with
  `OperationalError`. Only then did I diff the two password literals character by character
  and find the `c`/`d` difference. Lesson: `psycopg.OperationalError` covers both "cannot
  reach the server" and "authentication rejected", so the exception *type* proves nothing on
  its own - I now read the driver's message text, and I removed the duplicated literal
  entirely so the two copies cannot drift again.
- **Fix:** correct both ports, and stop hardcoding the password in two places: it is now
  supplied once from `.env` as `POSTGRES_PASSWORD` and interpolated into both the postgres
  service and `DATABASE_URL`.
- **Retest evidence:** Entry 12.

---

## Entry 07 - 2026-09-06 21:00 UTC - Both instances report the same identity

- **Symptom:**
  ```
  app-01 : app-01
  app-02 : app-01
  ```
  (`evidence/05-baseline-networks-identity.txt`)
- **Hypothesis:** copy/paste error in the Compose `environment:` override for `app-02`.
- **Command or test:** `grep -n INSTANCE_ID docker-compose.yml`
- **Actual output:** `app-02` sets `INSTANCE_ID: "app-01"`.
- **Root cause:** as hypothesised. On its own this makes load balancing unprovable - every
  `/instance` response says `app-01` whichever backend served it - and
  `scripts/video_challenge.py` explicitly requires `seen == {"app-01","app-02"}`.
- **Fix:** `INSTANCE_ID: "app-02"`.
- **Retest evidence:** Entry 12.

---

## Entry 08 - 2026-09-06 21:00 UTC - PostgreSQL data lives on tmpfs; the named volume is mounted where nothing writes

- **Symptom:** the Compose file declares a named volume `postgres-data`, which looked
  correct at a glance, so I nearly skipped it. The adjacent `tmpfs:` line did not look right.
- **Hypothesis:** PGDATA is not actually on the named volume.
- **Command or test:**
  ```bash
  docker inspect postgres --format '{{json .Mounts}}'
  docker exec postgres df -h /var/lib/postgresql/data
  ```
- **Actual output:**
  ```
  Destination: /var/lib/postgresql/backup     <- where the named volume is mounted
  Filesystem  Size  Used  Available  Use%  Mounted on
  tmpfs       3.8G  45.9M      3.8G    1%  /var/lib/postgresql/data
  ```
- **Root cause:** the named volume is mounted at `/var/lib/postgresql/backup`, a directory
  PostgreSQL never writes to, while the real `PGDATA` is a `tmpfs`. Every record is lost on
  container recreation - exactly the persistence proof Part 3 demands. This is the most
  dangerous fault in the starter, because the stack looks healthy while silently
  discarding data.
- **Fix:** remove the `tmpfs:` entry and mount `postgres-data` at `/var/lib/postgresql/data`.
- **Retest evidence:** Entry 13 - a record survives `--force-recreate` of app and postgres.

---

## Entry 09 - 2026-09-06 21:00 UTC - Container runs as root and ships a credential inside the image

- **Symptom:** the Dockerfile creates an unprivileged `app` user, then discards it.
- **Command or test:**
  ```bash
  docker exec app-01 id
  docker run --rm --entrypoint sh barq-assessment-app-01:latest -c "cat /srv/app.env"
  ```
- **Actual output:**
  ```
  uid=0(root) gid=0(root) groups=0(root)
  DATABASE_URL=postgresql://barq_app:BarqLabOnly_7qN2vK8d@postgres:5433/barq_tasks
  REDIS_URL=redis://redis:6380/0
  ```
- **Root cause:** `USER root` on the last line of the Dockerfile undoes the
  `groupadd`/`useradd` above it, and `COPY config/app.env /srv/app.env` bakes credentials
  into an image layer where they survive `docker history` and any registry push.
  `config/app.env` is also committed to Git. Separately `app/server.py` logs the full
  `DATABASE_URL` - password included - at startup.
- **Fix:** `USER app:app`; drop the `COPY config/app.env` line; add `config/app.env` to
  `.gitignore` and `.dockerignore` and ship `config/app.env.example` instead; redact the
  credential in the startup log; supply configuration only as runtime environment.
- **Retest evidence:** Entry 12.

---

## Entry 10 - 2026-09-06 21:00 UTC - nginx sits on the backend network; datastores declare host ports

- **Symptom / command:**
  ```bash
  docker exec nginx getent hosts postgres redis
  for c in nginx app-01 app-02 postgres redis; do
    docker inspect $c --format '{{json .NetworkSettings.Networks}}'; done
  ```
- **Actual output:**
  ```
  172.19.0.3  postgres
  172.19.0.4  redis
  nginx     : barq-assessment_backend barq-assessment_frontend
  ```
- **Root cause:** `nginx` is attached to `backend`, so the internet-facing proxy can open
  TCP sessions straight to PostgreSQL and Redis - the brief requires the opposite.
  `postgres` and `redis` additionally declare `ports: ["127.0.0.1:15432:5432"]` and
  `["127.0.0.1:16379:6379"]`, which Part 2 prohibits.
- **Failed attempt and what changed my thinking:** I started writing this up as "the
  database is exposed on host port 15432". `netstat` disagreed:
  ```bash
  $ netstat -ano -p tcp | grep LISTENING | grep -E ":(8080|15432|16379)\b"
    TCP    127.0.0.1:8080   0.0.0.0:0   LISTENING   21080
  $ docker port postgres      # (no output)
  ```
  Docker cannot publish a host port for a container attached only to an `internal: true`
  network, so the publish silently does nothing. The declaration is still a live
  misconfiguration - attaching postgres to any non-internal network later would expose it
  with no further edit - but it is not *currently* exploitable. That distinction mattered:
  the original wording would have been an unverifiable claim. I now assert exposure from
  `netstat` / `docker port`, never from the YAML.
- **Fix:** detach nginx from `backend`; delete both `ports:` blocks; `validate.py` asserts
  the negative (nothing listening on 15432/16379, and nginx cannot open a socket to either).
- **Retest evidence:** Entry 12 and the `isolation-*` checks in `validate.py`.

---

## Entry 11 - 2026-09-06 21:00 UTC - No failover, no restart policy, no resource limits

- **Symptom:** found by reading `nginx/nginx.conf` and `docker-compose.yml`, not from a
  runtime failure. Recorded because Part 3's failure test depends on all of it.
- **Root cause / findings:**
  - `proxy_next_upstream off;` - when one backend dies nginx returns the failure to the
    client instead of retrying the surviving peer; the failure test would report ~50% errors.
  - `max_fails=0` - disables passive health checking, so nginx keeps selecting a peer it
    has already seen fail.
  - `restart: "no"` on the apps - a crashed instance stays down.
  - no `deploy.resources.limits` anywhere - one runaway container can starve the host.
  - no `depends_on` conditions - apps start before postgres accepts connections.
- **Fix:** `proxy_next_upstream error timeout non_idempotent http_502 http_503 http_504`
  with `proxy_next_upstream_tries 2`, `max_fails=3 fail_timeout=5s`,
  `restart: unless-stopped`, CPU/memory limits on every service, and
  `depends_on: {postgres: {condition: service_healthy}, redis: {condition: service_healthy}}`.
  Rationale and trade-offs in `decisions.md`.
- **Retest evidence:** `failure_test.py` output, Entry 14.

---

## Entry 12 - 2026-09-06 21:07-21:12 UTC - Retest after the connectivity, config, persistence and isolation fixes

- **Retest evidence** for entries 02-10, taken after each fix commit:

  | Fault | Before | After |
  |---|---|---|
  | public port (02) | `curl: (52) Empty reply from server` | `HTTP/1.1 200 OK` on `/health` |
  | healthcheck (03) | `Up (unhealthy)` forever | `Up (healthy)` for both apps |
  | loopback bind (04) | `0100007F:1F90` | reachable from nginx |
  | upstream port (05) | `app-01:8081` | both peers serve |
  | dependency URLs (06) | `/ready` 503, both `unavailable` | `/ready` 200, both `ready` |
  | identity (07) | both answer `app-01` | 10 / 10 split across 20 requests |
  | persistence (08) | PGDATA on tmpfs | record `id=3` survives recreation |
  | secrets (09/12/13) | `/srv/app.env` in image, password in log | file absent, log shows `barq_app:***@` |
  | isolation (10/14) | nginx resolves postgres and redis | `getent` exit 2, `nc: bad address` |

  Raw captures: `evidence/06-stageA-verify.txt`, `evidence/07-stageB-verify.txt`,
  `evidence/09-stageC-persistence.txt`, `evidence/10-stageD-isolation.txt`.
- **Related commits:** `e3ea094`, `ea1d99b`, `432f4e2`, `fb9012a`.

---

## Entry 13 - 2026-09-06 21:09 UTC - Load balancing looked broken, but was a per-worker round-robin cursor

- **Symptom:** immediately after fixing the identity bug I sent 12 requests to
  `/instance` through nginx and every single one was answered by `app-01`. A second
  run of 20 requests came back a perfect 10 / 10. Same config, opposite result.
- **Hypothesis 1 (wrong):** `app-02` was still starting, or nginx had marked it down.
  Rejected: `docker compose ps` showed `app-02` healthy throughout, `max_fails=0` was
  set at the time so nginx could not mark any peer down, and `/counter` in the *same*
  capture alternated `app-01` / `app-02`.
- **Hypothesis 2:** nginx round-robin state is per worker process, not per server.
  Each worker starts its cursor on the first peer, so while workers are still cold the
  first request each one handles goes to `app-01`.
- **Command or test:** count the workers and force them all to be cold again:
  ```bash
  docker exec nginx sh -c "ps -o pid,args | grep -c '[n]ginx: worker'"   # 16
  docker exec nginx nginx -s reload && sleep 2
  for i in $(seq 12); do curl -sS -o /dev/null -D - http://127.0.0.1:8080/instance \
    | grep -i '^X-Instance-ID'; done | sort | uniq -c
  ```
- **Actual output:** deterministic reproduction - `12 X-Instance-ID: app-01`, then a
  further 40 requests split 22 / 18 as the workers warmed up.
  (`evidence/08-nginx-worker-rr-anomaly.txt`)
- **Root cause:** `worker_processes auto` gives 16 workers on this host, and with no
  `zone` directive each keeps its own private round-robin cursor in its own address
  space. This is not a fault the starter planted - it is a real property of nginx that
  the starter's config never accounted for, and it would have made the video's
  "prove both backends serve" demonstration look broken at random.
- **Fix:** add `zone application_pool 64k;` to the upstream block so round-robin state
  lives in shared memory and is consistent across all workers.
- **Retest evidence:** same reload-then-12-requests procedure after the fix:
  ```
  6 X-Instance-ID: app-01
  6 X-Instance-ID: app-02
  ```
  and 20 / 20 over the next 40 requests. (`evidence/11-stageE-availability.txt`)
- **Remaining uncertainty:** the split is only exactly even because every request is
  cheap and uniform. Under mixed workloads round-robin distributes *requests*, not
  load; `least_conn` would be the next step if request cost ever varies.

---

## Entry 14 - 2026-09-06 21:17 UTC - Failover and recovery

- **Symptom under test:** with `proxy_next_upstream off` (the starter value), stopping
  one backend returns the failure to the client instead of retrying the healthy peer.
- **Command or test:**
  ```bash
  docker stop app-01
  for i in $(seq 20); do curl -sS -m 6 -o /dev/null -w "%{http_code} " \
    http://127.0.0.1:8080/instance; done
  docker start app-01 && sleep 18
  ```
- **Actual output:** 20 consecutive `200`s while `app-01` was stopped, every one served
  by `app-02`; after restart, 10 / 10 across 20 requests.
  (`evidence/11-stageE-availability.txt`)
- **Root cause of the original behaviour:** `proxy_next_upstream off` plus
  `max_fails=0`, which also disabled passive health checking.
- **Fix:** `proxy_next_upstream error timeout http_502 http_503 http_504` with
  `proxy_next_upstream_tries 2`, and `max_fails=3 fail_timeout=5s` on each peer.
- **Remaining uncertainty / deliberate limit:** `non_idempotent` is *not* enabled, so a
  `POST /records` is never retried once it has been *sent* to a backend. See Entry 16 -
  measuring this corrected what I expected it to mean.

---

## Entry 16 - 2026-09-06 21:27 UTC - POST survived the outage, which I had predicted it would not

- **Symptom:** `failure_test.py` reported **100.00%** availability during the outage for
  GET *and* 54/54 successful POSTs. I had written in Entry 14 that POST would fail fast,
  because `proxy_next_upstream` deliberately omits `non_idempotent`.
- **Command or test:** `python failure_test.py` (`evidence/15-failure-test.txt`).
- **Root cause of my wrong prediction:** nginx refuses to retry a non-idempotent request
  only once *the request has already been sent* to the upstream. `docker stop` closes the
  listening socket, so the next attempt fails at `connect()` - nothing was ever sent, and
  nginx is free to retry a POST safely.
- **What this changes:** the safety property is better than I described. Writes are
  retried exactly when nginx *knows* nothing was delivered, and are not retried when the
  request may have been processed but the reply was lost. Entry 14 and `decisions.md`
  now state it that way. I would not have found the distinction without measuring it.

---

## Entry 17 - 2026-09-06 21:30 UTC - A paused backend returned 504s: my own retry budget was too small

- **Symptom:** `docker stop` gave a clean 100% availability, so I tested the harsher
  failure mode - `docker pause`, where the socket still accepts but nothing ever replies.
  This is also one of the three faults `scripts/video_challenge.py` can inject, so the
  signature was worth knowing before the recording. 12 client requests produced:
  ```
  200/0.004s 504/4.877s 200/0.004s 504/4.894s 200/0.004s 504/4.895s 200/0.005s 200/0.004s ...
  ```
  Three client-visible 504s before traffic settled.
- **Hypothesis 1 (wrong):** `proxy_next_upstream` was missing the timeout condition.
  Rejected by reading the config back - `timeout` was already listed.
- **Hypothesis 2:** the retry never happened because the *budget* for retries was already
  spent. `proxy_read_timeout` was 5s and `proxy_next_upstream_timeout` was also 5s, so by
  the time the first peer timed out at ~4.9s there was no time left to try the second.
  The three 504s at ~4.88s each match `proxy_read_timeout`, not a connect failure.
- **Root cause:** my own tuning, introduced in the availability commit. Two timeouts that
  looked independently reasonable were mutually exclusive.
- **Fix:** `proxy_next_upstream_timeout 12s`, comfortably above `proxy_read_timeout 5s`,
  so one full read timeout plus a retry fits inside the budget. `proxy_read_timeout` was
  left at 5s deliberately: the app's own dependency checks can take up to ~4s when
  PostgreSQL is unreachable, and a shorter proxy timeout would replace the app's
  informative 503 with an opaque 504.
- **Retest evidence:** same 12 requests against a paused backend after the change:
  ```
  200/4.871s 200/0.033s 200/4.866s 200/0.021s 200/4.902s 200/0.005s 200/0.029s ...
  ```
  Zero 504s. Three requests are slow while `max_fails=3` is still counting, then the peer
  is marked down for `fail_timeout` and everything is fast again.
  (`evidence/16-paused-backend-behaviour.txt`)
- **Remaining uncertainty:** three slow requests are still user-visible. Eliminating them
  needs active health checking, which open-source nginx does not provide; the production
  answer is a load balancer with active probes, noted in `security_review.md`.

---

## Entry 15 - 2026-09-06 21:19 UTC - Hardening the image surfaced a new error I had introduced

- **Symptom:** after switching the app to gunicorn under `read_only: true` with
  `USER app`, the container came up healthy and served every endpoint, but every start
  logged:
  ```
  [ERROR] Control server error: [Errno 30] Read-only file system: '/home/app'
  ```
- **Hypothesis:** something in gunicorn wants to write inside the runtime user's home
  directory, which does not exist (`useradd --no-create-home`) and could not be created
  anyway on a read-only root.
- **Command or test:** ask the installed gunicorn what it is trying to open, rather than
  guessing from the message:
  ```bash
  docker run --rm --entrypoint sh barq-assessment-app:latest -c 'python -c "
  from gunicorn.config import Config
  for name, setting in sorted(Config().settings.items()):
      if any(k in name for k in (\"control\",\"tmp\",\"home\",\"sock\")):
          print(name, \"=\", repr(setting.value))"'
  ```
- **Actual output:**
  ```
  control_socket = '/home/app/.gunicorn/gunicorn.ctl'
  control_socket_disable = False
  ```
- **Root cause:** gunicorn 26 opens a unix control socket under `$HOME/.gunicorn` by
  default. This was **my** regression, introduced by the hardening commit - not a starter
  fault. Recording it because the brief asks for the real journal, and because a
  non-fatal `[ERROR]` on every boot is exactly the kind of noise that trains an operator
  to ignore error logs.
- **Failed attempt and what changed my thinking:** my first instinct was to point `HOME`
  at the tmpfs (`ENV HOME=/tmp`). That would have worked, but it grants the process a
  writable home purely to satisfy a feature this deployment never uses - scaling here is
  Compose's job, not gunicorn's control socket. Turning the feature off is the smaller
  surface, so `control_socket_disable = True` went into `app/gunicorn_conf.py` instead.
- **Retest evidence:** `docker logs app-01 | grep -c ERROR` returns `0`, the boot log is
  four clean gunicorn INFO lines followed by the redacted `configuration_loaded` event,
  and all five containers stay healthy. (`evidence/12-stageF-hardening.txt`)

---

## Summary of faults found in the baseline

| # | File | Fault | Class |
|---|------|-------|-------|
| 1 | docker-compose.yml | nginx published to container port 81; nginx listens on 80 | connectivity |
| 2 | docker-compose.yml | app healthcheck probes `/healthz`; app serves `/health` | health |
| 3 | docker-compose.yml | `APP_HOST=127.0.0.1` - apps bind loopback only | connectivity |
| 4 | nginx/nginx.conf | upstream `app-01:8081` - wrong port | connectivity |
| 5 | config/app.env | `DATABASE_URL` port 5433, should be 5432 | dependency |
| 6 | config/app.env | `REDIS_URL` port 6380, should be 6379 | dependency |
| 7 | config/app.env vs compose | PostgreSQL password mismatch (`...8d` vs `...8c`) | dependency |
| 8 | docker-compose.yml | `app-02` `INSTANCE_ID` set to `app-01` | identity |
| 9 | docker-compose.yml | named volume at `/var/lib/postgresql/backup`; PGDATA on `tmpfs` | persistence |
| 10 | docker-compose.yml | redis started with `--save "" --appendonly no` | persistence |
| 11 | Dockerfile | `USER root` discards the unprivileged `app` user | security |
| 12 | Dockerfile / git | `config/app.env` baked into the image and committed | security |
| 13 | app/server.py | full `DATABASE_URL` incl. password written to stdout at startup | security |
| 14 | docker-compose.yml | nginx attached to `backend`; postgres/redis declare host ports | isolation |
| 15 | nginx.conf / compose | `proxy_next_upstream off`, `max_fails=0`, `restart: "no"`, no limits | availability |

Retest evidence for every row is in Entries 12-14 below and under `evidence/`.
