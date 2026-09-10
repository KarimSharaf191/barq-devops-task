# Evidence and submission index

## Submission

| | |
|---|---|
| Repository URL | https://github.com/KarimSharaf191/barq-devops-task |
| Final commit | `__________________` |
| Matching CI run | `__________________` |
| Continuous 12-18 minute video URL | `__________________` |
| Challenge receipt ID | `__________________` (from `.assessment/challenge.json`, created during the recording) |
| Starting video commit | `__________________` |
| Later documentation-only commits | All explained in [Later commits, and why they exist](#later-commits-and-why-they-exist); none changes application, Compose or NGINX behaviour |

Placeholders above are filled in once the repository is pushed and the video is recorded.
Everything below is already in the repository and verifiable now.

---

## Commit history

Progressive, one concern per commit: investigate, then fix, then verify. Every fix commit
names the faults it closes and the evidence file proving the retest.

| Commit | Type | What it does |
|---|---|---|
| `02fe9c8` | baseline | Initial assessment starter v1.0.0 (supplied) |
| `9b08964` | baseline | Release assessment starter v2.0.0 (supplied, tag `starter-v2.0.0`) |
| `349af2b`, `791f6b2`, `aa49fb3` | baseline | Supplied release commits, kept intact |
| `322ab4c` | investigate | Baseline failure evidence, 15 faults, no fixes yet |
| `e3ea094` | fix | Connectivity: nginx port, `APP_HOST`, upstream port, health path |
| `ea1d99b` | fix | Dependency URLs, password drift, secrets out of Git and the image, identity |
| `432f4e2` | fix | Persistence: PGDATA on the named volume, Redis AOF |
| `fb9012a` | fix | Network isolation: nginx off `backend`, datastore ports removed |
| `add6a06` | fix | Availability: failover, shared LB zone, restart policy, resource limits |
| `2eeca70` | fix | Image: non-root, gunicorn, stdlib healthcheck, read-only root |
| `df42b54` | feat | `validate.py`, `failure_test.py`, and the retry-budget fix they exposed |
| `2a87ccf` | feat | Verified `backup.sh` / `restore.sh` |
| `966dd6d` | ci | Build, start, validate and prove pipeline, plus a Trivy scan job |
| `d1185ca` | analysis | `scripts/analyze_logs.py` and all ten log answers |
| `6ce371f` | docs | README, decisions, security review, AI disclosure, diagram, this index |
| `800745a` | verify | Clean-state re-run of every gate the CI pipeline enforces |
| `494b89a` | chore | Ignore rules tightened; the two live video procedures rehearsed, then reverted |
| `8853d85` | docs | AI disclosure scope corrected |
| `9cb5c5b` | ci | Trivy action pinned to a tag that actually exists |
| `b7d3102` | ci | Trivy action pinned by commit SHA after an upstream tag was deleted |
| `a897dd6` | docs | Evidence file manifest, so no capture reads as missing |

### Later commits, and why they exist

The brief asks that any commit after the implementation work be explained. Every one of
them is documentation, verification or pipeline repair - no commit after `d1185ca`
changes application, Compose or NGINX behaviour, which `git diff` across that range will
confirm.

| Commit | Why it happened |
|---|---|
| `6ce371f` | The documentation set itself: README, decisions, security review, AI disclosure, generated diagram and this index. |
| `800745a` | A full clean-state verification run - stack torn down with `down -v` and every CI gate re-run locally - captured in `evidence/20`. |
| `494b89a` | Ignore rules tightened, and the two procedures performed live in the video rehearsed once and **reverted**, so both are genuinely performed on camera (`evidence/21`). |
| `8853d85` | The AI disclosure overstated where AI was used. Corrected rather than left standing, because an inaccurate disclosure is worse than a broad one. |
| `9cb5c5b`, `b7d3102` | The Trivy scan job could not start: it referenced `trivy-action@0.28.0`, which is not a real tag, and the `v0.28.0` release pins a `setup-trivy` tag that upstream has since deleted. Now pinned by commit SHA, which cannot be moved or deleted. **This was only discoverable after the first push** - a `uses:` reference is resolved by the runner, so no amount of local testing catches it. The gating job passed throughout; the scan is `continue-on-error` by design. |
| `a897dd6` | An evidence manifest covering all 22 capture files, added because a zero-byte capture and one uncited file could each have been read as missing evidence. |

Commits made during the recording are listed in the Submission table above, with their
video timestamps.

---

## Requirement -> evidence -> commit

### Part 1 - Investigation

| Requirement | File / output | Commit | Video |
|---|---|---|---|
| Baseline kept, committed before technical changes | tag `starter-v2.0.0`; `git diff starter-v2.0.0 -- logs/` is empty | `aa49fb3`, `322ab4c` | `__:__` |
| Progressive commits: investigate -> fix -> verify | `git log --oneline` (table above) | all | `__:__` |
| Symptoms, hypotheses, commands, results, failed attempts | [`troubleshooting.md`](../troubleshooting.md) - 17 entries | `322ab4c`+ | `__:__` |
| Root cause, fix, retest evidence per fault | `troubleshooting.md` summary table + entries 12-17 | each fix commit | `__:__` |
| Four wrong hypotheses recorded honestly | `troubleshooting.md` 01, 10, 16, 17 | `322ab4c`, `df42b54` | `__:__` |
| Two regressions I introduced, documented as mine | `troubleshooting.md` 15, 17 | `2eeca70`, `df42b54` | `__:__` |
| All three logs analysed, originals unchanged | [`log_analysis.md`](../log_analysis.md), [`scripts/analyze_logs.py`](../scripts/analyze_logs.py), [`evidence/18`](../evidence/18-log-analysis-output.txt) | `d1185ca` | `__:__` |
| Every log-template question answered | `log_analysis.md` sections 1-10 | `d1185ca` | `__:__` |
| Counts, timeline, correlation, double-count avoidance | `log_analysis.md` 2, 4, 6, 7, 8 | `d1185ca` | `__:__` |

### Part 2 - Docker, networking and NGINX

| Requirement | File / output | Commit | Video |
|---|---|---|---|
| Two Flask instances behind NGINX, working PostgreSQL and Redis | [`docker-compose.yml`](../docker-compose.yml), [`evidence/13`](../evidence/13-validate-pass.txt) | `e3ea094`, `ea1d99b` | `__:__` |
| Only NGINX published; app/PostgreSQL/Redis not published | `validate.py` `only-edge-publishes-a-host-port`, [`evidence/10`](../evidence/10-stageD-isolation.txt) | `fb9012a` | `__:__` |
| NGINX + apps on frontend; apps + datastores on backend | `validate.py` `isolation-*`, [`evidence/10`](../evidence/10-stageD-isolation.txt) | `fb9012a` | `__:__` |
| NGINX blocked from PostgreSQL/Redis | `getent` exit 2 inside nginx, [`evidence/10`](../evidence/10-stageD-isolation.txt) | `fb9012a` | `__:__` |
| Service names, not container IPs | `nginx/nginx.conf` upstream, `DATABASE_URL`/`REDIS_URL` | `ea1d99b` | `__:__` |
| Container names app-01, app-02, nginx, postgres, redis | `docker compose ps` | starter, kept | `__:__` |
| Network names ending frontend / backend | `barq-assessment_frontend`, `_backend` | starter, kept | `__:__` |
| Distinct app identities | [`evidence/07`](../evidence/07-stageB-verify.txt), 10/10 split | `ea1d99b` | `__:__` |
| Named PostgreSQL volume; Redis persistence | `validate.py` `postgres-pgdata-on-named-volume`, `redis-persistence-enabled` | `432f4e2` | `__:__` |
| Env vars, health/readiness, restart policies, resource limits | `docker-compose.yml`, `validate.py` `restart-policy-set` / `memory-limit-set` | `add6a06` | `__:__` |
| Non-root, minimal dependencies | `uid=10001(app)`, `uid=101(nginx)`, [`evidence/12`](../evidence/12-stageF-hardening.txt) | `2eeca70` | `__:__` |
| Health-check tools installed in the image, explained | [`app/healthcheck.py`](../app/healthcheck.py), busybox `wget` for nginx, [`decisions.md`](../decisions.md) 3 | `2eeca70` | `__:__` |
| Base-image choice explained | [`decisions.md`](../decisions.md) 1 | `2eeca70` | `__:__` |
| Secrets out of images, code and Compose; safe `.env.example` | [`.env.example`](../.env.example), [`evidence/07`](../evidence/07-stageB-verify.txt) | `ea1d99b` | `__:__` |
| All required endpoints, real DB/cache operations | [`evidence/12`](../evidence/12-stageF-hardening.txt), `validate.py` `endpoint-*` | `2eeca70` | `__:__` |

### Part 3 - Validation, persistence and CI

| Requirement | File / output | Commit | Video |
|---|---|---|---|
| `validate.py` with bounded waits, PASS/FAIL, non-zero exit | [`validate.py`](../validate.py), [`evidence/13`](../evidence/13-validate-pass.txt) - 46/46, exit 0 | `df42b54` | `__:__` |
| Validation actually fails when it should | [`evidence/14`](../evidence/14-validate-negative-tests.txt) - exit 1 twice | `df42b54` | `__:__` |
| Checks isolation and prohibited host ports | `validate.py` `isolation-*`, `prohibited-host-port-closed[*]` | `df42b54` | `__:__` |
| `failure_test.py`: stop, measure, restore, verify recovery | [`failure_test.py`](../failure_test.py), [`evidence/15`](../evidence/15-failure-test.txt) | `df42b54` | `__:__` |
| Traffic and errors measured during failure | [`evidence/15`](../evidence/15-failure-test.txt) - 100.00% availability, per-phase p50/p95 | `df42b54` | `__:__` |
| Recovered backend proven to serve again | [`evidence/15`](../evidence/15-failure-test.txt) phase 3 | `df42b54` | `__:__` |
| Harsher failure mode measured (paused backend) | [`evidence/16`](../evidence/16-paused-backend-behaviour.txt) - 3x504 before, 0 after | `df42b54` | `__:__` |
| `backup.sh` / `restore.sh`, restore proven | [`evidence/17`](../evidence/17-backup-restore-proof.txt) | `2a87ccf` | `__:__` |
| Record survives app + PostgreSQL container recreation | [`evidence/09`](../evidence/09-stageC-persistence.txt) | `432f4e2` | `__:__` |
| Exact test/backup/restore commands documented | [`README.md`](../README.md) | docs | `__:__` |
| CI on push and pull request | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | `966dd6d` | `__:__` |
| CI: checkout -> syntax -> build -> start -> wait -> validate | `ci.yml` `verify` job, 17 steps | `966dd6d` | `__:__` |
| CI fails when validation fails | `validate.py` exits 1; the step is not `continue-on-error` | `966dd6d` | `__:__` |
| Extra credit: image / security scan | `ci.yml` `scan` job (Trivy image + fs secrets/misconfig) | `966dd6d` | `__:__` |

### Part 4 - Documentation

| Requirement | File | Commit | Video |
|---|---|---|---|
| README: setup, build, start/stop, test, failure, backup/restore, cleanup | [`README.md`](../README.md) | docs | `__:__` |
| troubleshooting.md with failed attempts and retests | [`troubleshooting.md`](../troubleshooting.md) | `322ab4c`+ | `__:__` |
| log_analysis.md: all answers, commands, counts, correlation | [`log_analysis.md`](../log_analysis.md) | `d1185ca` | `__:__` |
| decisions.md: at least 5 decisions with trade-offs and limits | [`decisions.md`](../decisions.md) - 12 | docs | `__:__` |
| security_review.md: at least 8 concrete risks | [`security_review.md`](../security_review.md) - 14, of which 4 remain open | docs | `__:__` |
| Implemented fixes separated from production plans | `security_review.md` Part A vs Part B | docs | `__:__` |
| AI_USAGE.md | [`AI_USAGE.md`](../AI_USAGE.md) | docs | `__:__` |
| architecture.png / .pdf | [`architecture.png`](../architecture.png), [`.pdf`](../architecture.pdf), generated by [`scripts/make_architecture_diagram.py`](../scripts/make_architecture_diagram.py) | docs | `__:__` |
| All brief questions answered | [`README.md`](../README.md) - "Answers to the questions in the brief" | docs | `__:__` |

### Part 5 - Video demonstration

To be completed during the recording. Each row gets its timestamp and, where a change is
made live, its commit hash.

| Required live action | Planned evidence | Commit | Video |
|---|---|---|---|
| Repository, starting commit, clean `git status` | `git log -1`, `git status` | - | `__:__` |
| Build/start the stopped environment, show health | `docker compose up -d --wait`, `ps` | - | `__:__` |
| Test `/`, `/health`, `/ready`, `/records`, `/counter` | curl block from README | - | `__:__` |
| `/instance` proves both backends serve | 20-request loop, 10/10 split | - | `__:__` |
| Stop one backend, show traffic and errors, recover | `failure_test.py` | - | `__:__` |
| Record survives app + PostgreSQL recreation | README persistence block | - | `__:__` |
| Run validation and the failure test | `validate.py`, `failure_test.py` | - | `__:__` |
| Demonstrate one historical-log finding | `python scripts/analyze_logs.py --section 7` | - | `__:__` |
| Run `./video_challenge.sh` once, first time in this copy | `.assessment/challenge.json` receipt | - | `__:__` |
| Diagnose and fix the injected fault without `compose down` | live | `______` | `__:__` |
| Change public port 8080 -> 8090 live | `.env` edit + `up -d nginx` | `______` | `__:__` |
| Add a third instance live, prove all three respond | `app-03` in compose + nginx upstream + reload | `______` | `__:__` |
| Rerun validation with three instances | `validate.py --url http://127.0.0.1:8090` | - | `__:__` |
| `git status`, `git diff`, explain, commit on screen | live | `______` | `__:__` |
| Push video commits | live | `______` | `__:__` |

**What is known about the challenge before recording.** `scripts/video_challenge.py` is
readable and was read - the starter README explicitly permits this ("Read its code if
needed; do not run it early"). It has **not** been run; `.assessment/` does not exist in
this working copy. Its preflight requires every service healthy and unpaused, the exact
network layout, and both initial instances answering through the public URL - all of which
`validate.py` already asserts. It then injects **one of three** faults at random:
disconnect `app-02` from `frontend`, disconnect `redis` from `backend`, or pause `app-01`.
The diagnosis path for each is `docker inspect` network membership and `.State.Paused`,
and the repair is `docker network connect` or `docker unpause` - neither needs
`docker compose down`. The paused-backend signature was measured in advance and is in
[`evidence/16`](../evidence/16-paused-backend-behaviour.txt).

---

## Evidence file manifest

Every file in [`evidence/`](../evidence) is raw output captured at the moment the command
ran, numbered in the order the investigation happened. Nothing here is edited after
capture: where output is ugly, truncated or embarrassing, it stays that way, because an
edited capture is not evidence. 22 files, 89 KB, all plain text.

**Baseline - the broken state, before any fix**

| File | What it captures |
|---|---|
| [`00-compose-config.err`](../evidence/00-compose-config.err) | stderr of `docker compose config` at baseline. **This file is empty, and that is the finding**: the Compose file parsed without error, so the faults were never syntax. An empty capture is kept rather than deleted, because "no output" is itself the result. |
| [`01-baseline-build.txt`](../evidence/01-baseline-build.txt) | The image building from the starter Dockerfile |
| [`02-baseline-up.txt`](../evidence/02-baseline-up.txt) | The starter stack coming up for the first time |
| [`03-baseline-symptoms.txt`](../evidence/03-baseline-symptoms.txt) | The first symptom, exactly as encountered: `curl: (52) Empty reply from server`, plus the host listening sockets, port mappings and container logs taken in the same minute |
| [`04-baseline-deepdive.txt`](../evidence/04-baseline-deepdive.txt) | Inside the nginx container: nothing is listening on port 81, though Compose publishes it. Also the one place the dead baseline password still appears in tracked files - see `security_review.md` finding 1 |
| [`05-baseline-networks-identity.txt`](../evidence/05-baseline-networks-identity.txt) | Baseline network membership, showing nginx wrongly attached to **both** networks |

**Stage-by-stage retests - one capture per fix commit**

| File | What it captures |
|---|---|
| [`06-stageA-verify.txt`](../evidence/06-stageA-verify.txt) | Connectivity restored: `/health` and `/ready` answered through nginx, 10x `/instance` |
| [`07-stageB-verify.txt`](../evidence/07-stageB-verify.txt) | `/ready` reporting both PostgreSQL and Redis ready, and distinct app identities |
| [`08-nginx-worker-rr-anomaly.txt`](../evidence/08-nginx-worker-rr-anomaly.txt) | 16 nginx worker processes - the reason 12/12 requests appeared to hit one backend |
| [`09-stageC-persistence.txt`](../evidence/09-stageC-persistence.txt) | `PGDATA` resolved onto the named volume instead of the container filesystem |
| [`10-stageD-isolation.txt`](../evidence/10-stageD-isolation.txt) | Network membership after the fix: nginx on `frontend` only, and `getent` failing inside nginx |
| [`11-stageE-availability.txt`](../evidence/11-stageE-availability.txt) | nginx as uid 101, read-only root, round-robin fairness immediately after a reload (12/0 before the `zone` directive), and a 20-GET failover with app-01 stopped |
| [`12-stageF-hardening.txt`](../evidence/12-stageF-hardening.txt) | The app as uid 10001 under gunicorn, and every endpoint exercised against real PostgreSQL and Redis |

**Proofs of the automation**

| File | What it captures |
|---|---|
| [`13-validate-pass.txt`](../evidence/13-validate-pass.txt) | `validate.py` - 46 passed, 0 failed, exit 0 |
| [`14-validate-negative-tests.txt`](../evidence/14-validate-negative-tests.txt) | `validate.py` deliberately **failing** and exiting 1, twice, under two different induced faults. A validator that only ever passes proves nothing |
| [`15-failure-test.txt`](../evidence/15-failure-test.txt) | `failure_test.py` - baseline, outage and recovery phases, 100.00% GET and POST availability with per-phase p50/p95 |
| [`16-paused-backend-behaviour.txt`](../evidence/16-paused-backend-behaviour.txt) | The harsher failure mode: a *paused* backend, three client-visible 504s before the timeout fix and zero after, with the explanation of why a stopped backend never cost a POST |
| [`17-backup-restore-proof.txt`](../evidence/17-backup-restore-proof.txt) | A real restore: 210 rows in the dump, data changed afterwards, then the change proven undone |

**Log analysis**

| File | What it captures |
|---|---|
| [`18-log-analysis-output.txt`](../evidence/18-log-analysis-output.txt) | The full ten-section analyzer output over the three supplied log files |
| [`19-log-analysis-summary.json`](../evidence/19-log-analysis-summary.json) | The same run as machine-readable JSON: 720 distinct requests, 13.19% 5xx, p95 2.001s overall against 0.093s for successful requests alone |

**Final checks**

| File | What it captures |
|---|---|
| [`20-final-clean-run.txt`](../evidence/20-final-clean-run.txt) | Every gate the CI pipeline enforces, re-run locally against a stack rebuilt from an empty state (`down -v`, then `up --build --wait`) |
| [`21-live-procedures-dry-run.txt`](../evidence/21-live-procedures-dry-run.txt) | A rehearsal of the two procedures performed live in the video - the third instance and the 8080 -> 8090 port change - both **reverted afterwards**, so the repository ships two instances on 8080 and both actions are genuinely performed on camera |

## How to re-verify everything from a clean checkout

```bash
cp .env.example .env          # then set POSTGRES_PASSWORD
docker compose -p barq-assessment up -d --build --wait
python validate.py            # 46 checks, exit 0
python failure_test.py        # three phases, exit 0
./backup.sh && FORCE=1 ./restore.sh
python scripts/analyze_logs.py
python -m unittest discover -s tests
git diff --stat starter-v2.0.0 -- logs/    # empty: originals untouched
```
