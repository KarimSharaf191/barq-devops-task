"""Gunicorn settings for the containerised BARQ API.

The starter pinned gunicorn in requirements.txt but started the app with Flask's
development server, which is single-process, prints a runtime warning and is not
built to face a proxy. Serving the same WSGI callable under gunicorn keeps every
endpoint identical and makes the dependency honest.
"""
import os

bind = "{}:{}".format(os.getenv("APP_HOST", "0.0.0.0"), os.getenv("APP_PORT", "8080"))

# gthread rather than sync: the request handlers spend nearly all their time
# waiting on PostgreSQL or Redis, so threads buy concurrency for very little
# memory. Two workers keeps a request in flight while one is recycled, and stays
# inside the 0.5 CPU / 256M limit set for the app services in compose.
worker_class = "gthread"
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
threads = int(os.getenv("WEB_THREADS", "4"))

# The container root filesystem is read-only; /tmp is a tmpfs, which is also the
# right place for the worker heartbeat file (it must never hit real disk).
worker_tmp_dir = "/tmp"

# gunicorn 26 opens a unix control socket under $HOME/.gunicorn by default. The
# runtime user has no home directory and the root filesystem is read-only, so the
# master logged "Control server error: [Errno 30] Read-only file system:
# '/home/app'" on every start. Nothing in this deployment drives gunicorn through
# that socket - scaling is Compose's job - so the socket is switched off rather
# than given a writable path it does not need.
control_socket_disable = True

# 30s is comfortably above the app's own 2s dependency timeouts, so a worker is
# only killed for a genuine hang, never for a slow database call.
timeout = 30
graceful_timeout = 10
# Above nginx's keepalive so the proxy, not the backend, closes idle connections.
keepalive = 35

# The application already emits one structured JSON line per request from its
# after_request hook. A gunicorn access log would duplicate every one of them and
# make request counting in the logs ambiguous.
accesslog = None
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")
