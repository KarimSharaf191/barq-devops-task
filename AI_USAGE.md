# AI usage disclosure

**AI was used extensively on this submission.** This document says where, what it
produced, what I rejected, and - most importantly - how each claim was verified against
the running system rather than accepted because it sounded right.

- **Tool/model:** a large language model coding assistant, used in an agentic terminal
  session with shell, Docker and file access on this machine.
- **Scope:** the whole repository except the supplied starter files. Everything under
  `logs/`, `assessment/`, `scripts/video_challenge.py`, `video_challenge.sh`,
  `app/server.py`'s original endpoint logic, `tests/test_app.py`'s original cases and
  `database/init.sql` came from BARQ and is unchanged except where noted below.

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
| 3 | POST will fail during a backend outage, because `non_idempotent` is not set | 54/54 POSTs succeeded. nginx only withholds a retry once the request has been *sent*; a refused connect is safe to retry. My model of the directive was wrong. | `troubleshooting.md` 16 |
| 4 | Load balancing is broken - 12/12 requests hit `app-01` | The identities were right; nginx keeps a **per-worker** round-robin cursor and there were 16 workers. Reproduced deterministically with `nginx -s reload`, fixed with a shared `zone`. | `troubleshooting.md` 13 |

Two further bugs were introduced **by** the AI-assisted work and caught the same way:
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
  in `evidence/00`-`evidence/19`.

### Environment repair (`docker-compose.yml`, `nginx/nginx.conf`, `Dockerfile`, `app/`)

- **Purpose:** proposing fixes for the 15 faults and the hardening around them.
- **What I changed or rejected:** rejected Alpine as the app base (musl would force a
  source build of `psycopg` for no real gain); rejected `ENV HOME=/tmp` to silence the
  gunicorn control-socket error in favour of disabling a feature this deployment does not
  use; rejected `non_idempotent` on `proxy_next_upstream` because a duplicated `INSERT`
  is worse than a 504 the client can retry knowingly. Reasoning in `decisions.md`.
- **How verified:** each fix was applied and re-tested in its own commit before the next
  one started, so no fix is claimed on the strength of a later, unrelated green run. The
  46-check `validate.py` and the app-only unit suite both pass.

### Validation, failure and backup scripts

- **Purpose:** drafting `validate.py`, `failure_test.py`, `backup.sh` and `restore.sh`.
- **What I changed or rejected:** rejected hard-coded container names in favour of
  Compose discovery, both because `scripts/README.md` warns against touching another
  project and because names like `postgres` and `nginx` are exactly what a *different*
  project on this machine would also be using. Added the `finally` block that restarts
  the victim in `failure_test.py` after considering what a Ctrl+C mid-test would leave
  behind.
- **How verified:** a validator that only ever passes proves nothing, so it was tested
  **negatively**: with `app-02` stopped it fails 2 checks and exits 1; with nginx wrongly
  attached to the backend network it fails 3 isolation checks and exits 1; restored, it
  passes 46/46 and exits 0 (`evidence/14-validate-negative-tests.txt`). The restore was
  likewise proved by changing the data first and confirming the change was undone
  (`evidence/17-backup-restore-proof.txt`).

### Log analysis (`log_analysis.md`, `scripts/analyze_logs.py`)

- **Purpose:** writing the parser and drafting the answers.
- **What I changed or rejected:** rejected deduplicating on `request_id`, which would
  have discarded the legitimate second application-log row that every dependency failure
  writes. Rejected quoting a single p95, because the 5xx sample is bimodal and one number
  hides that.
- **How verified:** every headline number was computed **twice by independent means** -
  once by the script, once with `grep`/`sort`/`uniq` - and the two are shown side by side
  in `log_analysis.md`. Two parser bugs were found precisely because the methods
  disagreed: an `error.log` regex that silently dropped the `[notice]` rotation line, and
  a `grep -c '^{'` that counted 721 because the truncated line also starts with a brace.
  The strongest check is structural: 680 unique application records + 40 requests with no
  application record = 720 access-log requests, from two different files.

### Documentation and the architecture diagram

- **Purpose:** drafting `README.md`, `decisions.md`, `security_review.md`,
  `docs/EVIDENCE_INDEX.md`, and generating `architecture.png` / `architecture.pdf`.
- **What I changed or rejected:** rejected a security review that listed only solved
  problems - Part B of `security_review.md` lists four risks that remain **open** in what
  I am submitting, including unauthenticated Redis, which I name as the weakest item I
  did not fix. Rejected the first diagram, whose labels overflowed their boxes; the
  diagram is now generated by a committed script so it cannot drift from the Compose file.
- **How verified:** every command in `README.md` was executed from a clean checkout path
  before being written down. The diagram was rendered and read back three times, and its
  contents were checked line by line against `docker-compose.yml` and `nginx/nginx.conf`.

## What is not AI-generated

The three log fixtures, the assessment briefs, `scripts/video_challenge.py`,
`video_challenge.sh`, `database/init.sql`, the original endpoint implementations in
`app/server.py` and the original eight cases in `tests/test_app.py` are BARQ's, unchanged
apart from the specific edits listed in the commit history (`redact_url` and its three
tests; `app/healthcheck.py` and `app/gunicorn_conf.py` are new files).

## Honest statement of what this means

The AI wrote most of the prose and most of the first-draft code. It did not decide what
was true. Every fault in `troubleshooting.md` was confirmed against a running container
before it was written down, four confident claims were overturned by measurement, and two
regressions introduced during the work are documented as mine. I can reproduce any number
in this repository on request, and the video demonstrates the environment live rather
than reciting these documents.
