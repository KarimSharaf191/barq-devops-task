"""Container liveness probe for the BARQ API.

Deliberately uses nothing but the Python standard library that the runtime image
already ships. Adding curl or wget to a python:slim image would mean an extra apt
layer and extra attack surface for a job the interpreter can already do.

Exit 0 when the process is alive and answering /health, non-zero otherwise.
"""
import json
import os
import sys
import urllib.request

DEFAULT_TIMEOUT = 2.0


def probe(host=None, port=None, timeout=None):
    host = host or os.getenv("HEALTHCHECK_HOST", "127.0.0.1")
    port = port or os.getenv("APP_PORT", "8080")
    timeout = timeout or float(os.getenv("HEALTHCHECK_TIMEOUT", DEFAULT_TIMEOUT))
    url = "http://{}:{}/health".format(host, port)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return 1, "{} returned HTTP {}".format(url, response.status)
            body = json.load(response)
    except Exception as exc:                                  # noqa: BLE001 - any failure is unhealthy
        return 1, "{}: {}: {}".format(url, type(exc).__name__, exc)
    if body.get("status") != "alive":
        return 1, "{} returned unexpected body {!r}".format(url, body)
    return 0, "{} alive as {}".format(url, body.get("instance_id"))


def main():
    code, message = probe()
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
