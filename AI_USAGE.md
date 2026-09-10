# AI usage disclosure

**AI was used on this submission in three areas: investigation and diagnosis,
documentation, and the architecture diagram.** This document says where, what it
produced, what I rejected, and - most importantly - how each claim was verified against
the running system rather than accepted because it sounded right. It also states plainly
which parts of the repository were **not** AI-assisted.

- **Tool/model:** Claude Code (Anthropic), used in an agentic terminal session with
  shell, Docker and file access on this machine.
- **Where it was used:** `troubleshooting.md` and the hypothesis-generation behind
  `evidence/`; the prose of `README.md`, `decisions.md`, `security_review.md`,
  `docs/EVIDENCE_INDEX.md` and this file; and
  `scripts/make_architecture_diagram.py`, which generates `architecture.png` /
  `architecture.pdf`.
- **Where it was not used:** the environment repair itself (`docker-compose.yml`,
  `nginx/nginx.conf`, `Dockerfile`, `app/`), the validation, failure and backup scripts
  (`validate.py`, `failure_test.py`, `backup.sh`, `restore.sh`) and the log parser
  (`scripts/analyze_logs.py`). Those I wrote.
- **Supplied by BARQ and unchanged except where noted:** everything under `logs/`,
  `assessment/`, `scripts/video_challenge.py`, `video_challenge.sh`, `app/server.py`'s
  original endpoint logic, `tests/test_app.py`'s original cases and `database/init.sql`.

## The verification rule I applied

**Nothing is claimed in this repository unless a command was run and its output
captured.** That rule is the reason `evidence/` exists. Every number in
`troubleshooting.md`, `log_analysis.md`, `decisions.md` and `security_review.md` is
traceable to a file in `evidence/` or to a script anyone can re-run. Where AI proposed
something I could not verify, it was cut rather than softened.

This mattered repeatedly, because the AI-assisted first answer was wrong four times, and
each time the *measurement* is what corrected it:

| # | The confident claim | What measuring actually showed | Where |
|---|---|---|---|
| 1 | `requirements.txt` pins invented versions, so the build fails | Every pin resolves. The faults are configuration, not packaging. I nearly rewrote a file that was fine. | `troubleshooting.md` 01 |
| 2 | The database is exposed on host port 15432 | `netstat` and `docker port` showed nothing listening. Docker cannot publish a port for a container on an `internal` network, so the declaration was inert. The finding was rewritten as latent, not live. | `troubleshooting.md` 10, `security_review.md` 5 |
| 3 | POST will fail during a backend outage, because `non_idempotent` is not set | 54/54 POSTs succeeded. nginx only withholds a retry once the request has been *sent*; a refused connect is safe to retry. The model of the directive was wrong. | `troubleshooting.md` 16 |
| 4 | Load balancing is broken - 12/12 requests hit `app-01` | The identities were right; nginx keeps a **per-worker** round-robin cursor and there were 16 workers. Reproduced deterministically with `nginx -s reload`, fixed with a shared `zone`. | `troubleshooting.md` 13 |

Two further bugs were introduced by **my** hardening work and caught by the same rule:
the gunicorn control-socket error under a read-only root (`troubleshooting.md` 15), and
`proxy_next_upstream_timeout` set equal to `proxy_read_timeout`, which silently made the
retry impossible and produced three client-visible 504s (`troubleshooting.md` 17). Both
are documented as my regressions, not as starter faults, because that is what they were.

## Per-area detail

### Investigation and diagnosis (`troubleshooting.md`, `evidence/`)

- **Purpose:** generating hypotheses, and turning each one into a command that would
  falsify it.
- **What I changed or rejected:** the four reversals in the table above. I also rejected
  "read the YAML and report what it says" as a method - findings 2 and 4 both show that
  configuration text and runtime behaviour disagree, so every isolation and exposure
  claim is asserted from `netstat`, `docker port`, `/proc/net/tcp` or `getent` inside the
  container.
- **How verified:** every entry cites the command and its real output; raw captures are
  in `evidence/00`-`evidence/21`, listed file by file with what each one proves in
  [`docs/EVIDENCE_INDEX.md`](docs/EVIDENCE_INDEX.md). The commands themselves were run by
  me, and a hypothesis survived into `troubleshooting.md` only once its output was on
  disk.

### Documentation and the architecture diagram

- **Purpose:** drafting the prose of `README.md`, `decisions.md`, `security_review.md`,
  `docs/EVIDENCE_INDEX.md` and this file, and generating `architecture.png` /
  `architecture.pdf`.
- **What I changed or rejected:** rejected a security review that listed only solved
  problems - Part B of `security_review.md` lists four risks that remain **open** in what
  I am submitting, including unauthenticated Redis, which I name as the weakest item I
  did not fix. Rejected the first diagram, whose labels overflowed their boxes; the
  diagram is now generated by a committed script so it cannot drift from the Compose file.
- **How verified:** every command in `README.md` was executed from a clean checkout path
  before being written down. The diagram was rendered and read back three times, and its
  contents were checked line by line against `docker-compose.yml` and `nginx/nginx.conf`.
  The engineering claims the prose describes are mine, and each one is backed by a file
  in `evidence/`.

## What is not AI-generated

Beyond the BARQ-supplied files listed at the top, the following are my own work:

- **The environment repair.** The 15 fixes and the hardening around them in
  `docker-compose.yml`, `nginx/nginx.conf`, `Dockerfile` and `app/`, including the
  liveness/readiness split, the two-network separation, the shared round-robin `zone`,
  the retry and timeout ladder, the non-root gunicorn image and the stdlib healthcheck.
  The reasoning behind each is in `decisions.md`.
- **The validation, failure and backup scripts.** `validate.py`, `failure_test.py`,
  `backup.sh` and `restore.sh` - including the Compose-based service discovery used
  instead of hard-coded container names, and the `finally` block that restarts the
  victim after a Ctrl+C mid-test.
- **The log parser.** `scripts/analyze_logs.py` and the analysis in `log_analysis.md`,
  including the decision not to deduplicate on `request_id` and not to quote a single
  p95 over a bimodal 5xx sample.

`redact_url` and its three tests, `app/healthcheck.py` and `app/gunicorn_conf.py` are new
files of mine; the rest of `app/server.py` and `tests/test_app.py` is BARQ's, unchanged
apart from the specific edits listed in the commit history.

## Honest statement of what this means

AI drafted most of the prose in the reports, generated the diagram script, and pushed
hard on hypothesis generation during the investigation. It did not write the environment
fixes, the validation, failure, backup or log-analysis code, and it did not decide what
was true. Every fault in `troubleshooting.md` was confirmed against a running container
before it was written down, four confident claims were overturned by measurement, and two
regressions introduced during my hardening are documented as mine. I can reproduce any
number in this repository on request, and the video demonstrates the environment live
rather than reciting these documents.
