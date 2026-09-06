#!/usr/bin/env python3
"""Render architecture.png and architecture.pdf from this one source of truth.

The diagram is generated, not drawn by hand, so it cannot drift away from the
Compose file without someone editing this script on purpose.

    python -m pip install matplotlib
    python scripts/make_architecture_diagram.py

Shows the FINAL state the submission is matched to: three app instances behind
NGINX, published on host port 8090.
"""
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as path_effects                              # noqa: E402
import matplotlib.pyplot as plt                                           # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

INK, MUTED = "#161b22", "#59626f"
EDGE_C, APP_C, DATA_C, HOST_C, STOP_C = "#1d6fb8", "#2f8f4e", "#b4530a", "#6d4aa8", "#c22626"
FRONT_BG, BACK_BG, CARD_BG = "#e9f2fb", "#fdefe3", "#ffffff"

TITLE_DROP, LINE_TOP, LINE_STEP = 0.30, 0.70, 0.30


def card(ax, x, y, w, h, title, lines, colour, bg=CARD_BG, title_size=10.5):
    """Draw a labelled box. Returns (centre_x, centre_y)."""
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.12,rounding_size=0.10",
        linewidth=1.9, edgecolor=colour, facecolor=bg, zorder=3))
    if title:
        ax.text(x + w / 2, y + h - TITLE_DROP, title, ha="center", va="center",
                fontsize=title_size, fontweight="bold", color=colour, zorder=4)
    for index, line in enumerate(lines):
        ax.text(x + w / 2, y + h - LINE_TOP - index * LINE_STEP, line,
                ha="center", va="center", fontsize=7.7, color=INK,
                zorder=4, family="monospace")
    return (x + w / 2, y + h / 2)


def wire(ax, points, colour, linewidth=1.7, dashed=False):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    ax.plot(xs, ys, color=colour, linewidth=linewidth, zorder=5, solid_capstyle="round",
            linestyle=(0, (4, 3)) if dashed else "solid")


def head(ax, start, end, colour, dashed=False, rad=0.0, style="-|>"):
    ax.add_patch(FancyArrowPatch(
        start, end, arrowstyle=style, mutation_scale=13, linewidth=1.7,
        color=colour, zorder=6, shrinkA=0, shrinkB=0,
        connectionstyle="arc3,rad={}".format(rad),
        linestyle=(0, (4, 3)) if dashed else "solid"))


def tag(ax, x, y, text, colour, size=7.6, weight="bold", ha="center"):
    label = ax.text(x, y, text, ha=ha, va="center", fontsize=size,
                    color=colour, fontweight=weight, zorder=7)
    label.set_path_effects([path_effects.withStroke(linewidth=3.4, foreground="white")])


def build():
    fig, ax = plt.subplots(figsize=(15.2, 11.0), dpi=170)
    ax.set_xlim(0, 15.2)
    ax.set_ylim(0, 11.0)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    ax.text(0.35, 10.63, "BARQ DevOps assessment - final architecture",
            fontsize=17.5, fontweight="bold", color=INK)
    ax.text(0.35, 10.26,
            "three app instances behind NGINX, published on 127.0.0.1:8090    |    "
            "Compose project: barq-assessment",
            fontsize=9.8, color=MUTED)

    # ---------------------------------------------------------------- host --
    ax.add_patch(Rectangle((0.30, 0.30), 14.55, 9.62, linewidth=1.5,
                           edgecolor=HOST_C, facecolor="none",
                           linestyle=(0, (7, 4)), zorder=1))
    ax.text(0.50, 9.72, "HOST   Windows 11 + Docker Desktop (WSL2, Linux containers)",
            fontsize=8.9, color=HOST_C, fontweight="bold", zorder=2)

    card(ax, 0.70, 8.20, 2.70, 1.25, "Client / curl",
         ["browser, curl,", "validate.py"], HOST_C)

    ax.text(3.75, 9.20, "THE ONLY PUBLISHED PORT", fontsize=8.6,
            color=HOST_C, fontweight="bold")
    ax.text(3.75, 8.90, "127.0.0.1:8090  ->  nginx:8080", fontsize=9.0,
            color=INK, family="monospace")
    ax.text(3.75, 8.60,
            "app-01/02/03, postgres and redis publish nothing to the host",
            fontsize=7.8, color=MUTED, style="italic")
    ax.text(3.75, 8.34,
            "8090 is the post-video value; the stack shipped on 8080 (PUBLIC_PORT in .env)",
            fontsize=7.2, color=MUTED, style="italic")

    # ------------------------------------------------------------ frontend --
    ax.add_patch(FancyBboxPatch(
        (0.70, 4.62), 13.75, 3.42, boxstyle="round,pad=0.10,rounding_size=0.10",
        linewidth=1.7, edgecolor=EDGE_C, facecolor=FRONT_BG, zorder=1))
    ax.text(0.92, 7.86, "barq-assessment_frontend   (bridge)",
            fontsize=9.4, color=EDGE_C, fontweight="bold", zorder=2)

    card(ax, 1.00, 4.95, 3.05, 2.55, "nginx",
         ["nginx:1.28-alpine", "user 101:101, read-only", "listen 8080",
          "zone application_pool", "max_fails=3 / 5s", "read 5s, retry budget 12s"],
         EDGE_C)

    app_x = [4.95, 8.10, 11.25]
    for index, name in enumerate(("app-01", "app-02", "app-03")):
        card(ax, app_x[index], 4.95, 2.90, 2.55, name,
             ["gunicorn gthread", "uid 10001 (app)", "read-only + /tmp tmpfs",
              "0.0.0.0:8080", "cpu 0.5 / mem 256M",
              "added LIVE in video" if index == 2 else "restart unless-stopped"],
             APP_C)
    app_centres = [x + 1.45 for x in app_x]

    # ------------------------------------------------------------- backend --
    ax.add_patch(FancyBboxPatch(
        (4.35, 0.50), 10.10, 3.32, boxstyle="round,pad=0.10,rounding_size=0.10",
        linewidth=1.7, edgecolor=DATA_C, facecolor=BACK_BG, zorder=1))
    ax.text(4.57, 3.66, "barq-assessment_backend   (internal: true)",
            fontsize=9.4, color=DATA_C, fontweight="bold", zorder=2)
    ax.text(4.57, 3.44, "internal => no route to the internet",
            fontsize=7.4, color=DATA_C, style="italic", zorder=2)

    card(ax, 4.80, 1.30, 4.40, 2.00, "postgres",
         ["postgres:16-alpine", "5432, not published",
          "PGDATA=.../data/pgdata", "pg_isready healthcheck",
          "cpu 1.0 / mem 512M"], DATA_C)
    card(ax, 9.60, 1.30, 4.40, 2.00, "redis",
         ["redis:7.4-alpine", "6379, not published",
          "appendonly yes, everysec", "maxmemory-policy noeviction",
          "cpu 0.5 / mem 192M"], DATA_C)

    ax.text(7.00, 0.92, "named volume  barq-assessment_postgres-data",
            ha="center", fontsize=7.6, color=DATA_C, family="monospace", zorder=4)
    ax.text(11.80, 0.92, "named volume  barq-assessment_redis-data",
            ha="center", fontsize=7.6, color=DATA_C, family="monospace", zorder=4)
    head(ax, (7.00, 1.30), (7.00, 1.06), DATA_C, style="<|-|>")
    head(ax, (11.80, 1.30), (11.80, 1.06), DATA_C, style="<|-|>")

    # ----------------------------------------------------- client -> nginx --
    head(ax, (2.05, 8.20), (2.05, 7.52), HOST_C)
    tag(ax, 2.62, 8.10, "HTTP :8090", HOST_C, size=8.0, ha="left")

    # ------------------------------------------- nginx -> apps (upper bus) --
    bus_y = 7.72
    wire(ax, [(2.52, 7.50), (2.52, bus_y), (app_centres[-1], bus_y)], EDGE_C)
    for centre in app_centres:
        head(ax, (centre, bus_y), (centre, 7.52), EDGE_C)
    tag(ax, 6.45, bus_y + 0.20, "proxy_pass  ->  round robin over all instances", EDGE_C)

    # ------------------------------------------ apps -> datastores (lower) --
    drop_y = 4.24
    wire(ax, [(app_centres[0], 4.95), (app_centres[0], drop_y)], APP_C)
    wire(ax, [(app_centres[1], 4.95), (app_centres[1], drop_y)], APP_C)
    wire(ax, [(app_centres[2], 4.95), (app_centres[2], drop_y)], APP_C)
    wire(ax, [(app_centres[0], drop_y), (app_centres[2], drop_y)], APP_C)
    head(ax, (8.30, drop_y), (8.30, 3.34), APP_C)
    head(ax, (11.80, drop_y), (11.80, 3.34), APP_C)
    tag(ax, 8.45, 4.02, "SQL 5432", APP_C, ha="left")
    tag(ax, 11.95, 4.02, "Redis 6379", APP_C, ha="left")
    tag(ax, 9.55, drop_y + 0.17, "every instance reaches both datastores by SERVICE NAME", APP_C)

    # -------------------------------------------------- the blocked path ----
    head(ax, (2.52, 4.95), (4.62, 3.92), STOP_C, dashed=True, rad=-0.22)
    ax.plot([3.46], [4.31], marker="x", markersize=15, color=STOP_C,
            markeredgewidth=3.4, zorder=8)
    tag(ax, 0.78, 4.50, "BLOCKED", STOP_C, size=8.4, ha="left")
    tag(ax, 0.78, 4.30, "nginx is not on", STOP_C, size=7.6, ha="left")
    tag(ax, 0.78, 4.12, "the backend network", STOP_C, size=7.6, ha="left")

    # ------------------------------------------------------------- legend ---
    ax.add_patch(FancyBboxPatch(
        (0.70, 0.50), 3.35, 3.32, boxstyle="round,pad=0.10,rounding_size=0.10",
        linewidth=1.4, edgecolor=MUTED, facecolor="#f6f8fa", zorder=1))
    ax.text(0.90, 3.62, "Health and readiness", fontsize=9.4,
            color=INK, fontweight="bold", zorder=2)

    rows = [
        ("GET /health", EDGE_C, [
            "liveness only, no dependency call.",
            "Docker probes THIS, so a Redis blip",
            "cannot restart a healthy app."]),
        ("GET /ready", EDGE_C, [
            "PostgreSQL + Redis: 200 or 503.",
            "Used by humans and validate.py,",
            "never by Docker."]),
        ("GET /nginx-health", EDGE_C, [
            "edge liveness, answered by nginx",
            "itself - no backend is touched."]),
    ]
    y = 3.28
    for name, colour, body in rows:
        ax.text(0.90, y, name, fontsize=8.0, color=colour, family="monospace",
                fontweight="bold", zorder=2)
        y -= 0.24
        for line in body:
            ax.text(1.02, y, line, fontsize=7.3, color=MUTED, zorder=2)
            y -= 0.225
        y -= 0.10

    ax.add_patch(FancyBboxPatch(
        (10.05, 8.26), 4.35, 1.14, boxstyle="round,pad=0.10,rounding_size=0.08",
        linewidth=1.3, edgecolor=STOP_C, facecolor="#fdf0f0", zorder=2))
    ax.text(10.25, 9.18, "Single points of failure that remain",
            fontsize=8.2, color=STOP_C, fontweight="bold", zorder=3)
    for index, line in enumerate([
            "one nginx, one postgres, one redis,",
            "one host, one volume. Only the app tier",
            "is redundant - security_review.md 13."]):
        ax.text(10.25, 8.94 - index * 0.21, line, fontsize=7.3, color=STOP_C, zorder=3)

    for suffix in ("png", "pdf"):
        target = ROOT / "architecture.{}".format(suffix)
        fig.savefig(target, facecolor="white", bbox_inches="tight", pad_inches=0.25)
        print("wrote {}".format(target))
    plt.close(fig)


if __name__ == "__main__":
    build()
