# Base image: the same digest-pinned python:3.12-slim-bookworm the starter used.
# Kept deliberately - slim gives a Debian userland (so psycopg[binary] wheels and
# glibc just work) at roughly a tenth of the full python image, and the digest pin
# means "rebuild" can never silently mean "different base". Alpine would swap glibc
# for musl and force a source build of psycopg for no measurable size win here.
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

# Requirements first: this layer is cached until the pins actually change.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app app/ ./app/

# Drop privileges for the runtime. The starter created this account and then threw
# it away with a trailing USER root.
USER app:app

EXPOSE 8080

# The probe is a stdlib-only Python module that is already inside the image, so no
# extra package (curl, wget) and no extra attack surface is added just to answer
# "is the process alive?". It calls /health, which never touches a dependency, so
# a PostgreSQL or Redis outage cannot make Docker restart a perfectly healthy app.
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

# gunicorn, not the Flask development server. Same WSGI callable, same endpoints.
CMD ["gunicorn", "--config", "/srv/app/gunicorn_conf.py", "app.server:create_app()"]
