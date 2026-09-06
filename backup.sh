#!/usr/bin/env bash
# Take a verified logical backup of the assessment PostgreSQL database.
#
#   ./backup.sh                 # write backups/barq_tasks-<UTC timestamp>.dump
#   BACKUP_DIR=/tmp ./backup.sh # choose the output directory
#
# Uses pg_dump custom format (-Fc): compressed, restorable selectively, and the
# only format pg_restore can filter. The dump is written through a pipe to the
# host, so it never occupies space inside the container, and it is verified with
# pg_restore --list before being reported as usable - an empty or truncated file
# that nobody ever tried to read is not a backup.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# --- shared helpers -------------------------------------------------------- #
PROJECT="${COMPOSE_PROJECT_NAME:-barq-assessment}"
SERVICE="postgres"
BACKUP_DIR="${BACKUP_DIR:-./backups}"

die() { printf '%s\n' "ERROR: $*" >&2; exit 1; }
info() { printf '%s\n' "$*" >&2; }

# Resolve the container through Compose, never by bare name, so this script can
# only ever touch the PostgreSQL container of the project it was pointed at.
resolve_container() {
    local id
    id="$(docker compose -p "$PROJECT" ps -q "$SERVICE" 2>/dev/null || true)"
    [ -n "$id" ] || die "no '$SERVICE' container in Compose project '$PROJECT'. Is the stack up?"
    printf '%s\n' "$id"
}

# Read the credentials from the running container rather than from a file, so no
# secret is duplicated into these scripts or into the shell history.
container_env() {
    docker inspect "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' \
        | sed -n "s/^$2=//p" | head -n1
}

wait_for_postgres() {
    local id="$1" user="$2" db="$3" deadline=$(( $(date +%s) + ${PG_WAIT:-60} ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if docker exec "$id" pg_isready -h 127.0.0.1 -U "$user" -d "$db" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    die "PostgreSQL in $id was not ready within ${PG_WAIT:-60}s"
}

row_count() {
    docker exec "$1" psql -qtAX -U "$2" -d "$3" -c \
        'SELECT count(*) FROM records' 2>/dev/null | tr -d '[:space:]'
}

main() {
    local id user db stamp target rows
    id="$(resolve_container)"
    user="$(container_env "$id" POSTGRES_USER)"
    db="$(container_env "$id" POSTGRES_DB)"
    [ -n "$user" ] && [ -n "$db" ] || die "could not read POSTGRES_USER/POSTGRES_DB from $id"

    wait_for_postgres "$id" "$user" "$db"
    rows="$(row_count "$id" "$user" "$db")"

    mkdir -p "$BACKUP_DIR"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    target="$BACKUP_DIR/${db}-${stamp}.dump"

    info "project=$PROJECT container=${id:0:12} database=$db rows_in_records=${rows:-?}"
    info "dumping to $target"
    # --no-owner/--no-privileges keep the dump restorable into a database whose
    # role names differ, which is what happens on a rebuilt lab.
    docker exec "$id" pg_dump -U "$user" -d "$db" -Fc --no-owner --no-privileges > "$target"

    [ -s "$target" ] || die "dump file is empty: $target"
    if ! pg_restore --list "$target" >/dev/null 2>&1; then
        # pg_restore is not always installed on the host; fall back to the copy
        # inside the container image, which certainly matches the server version.
        docker exec -i "$id" pg_restore --list < "$target" >/dev/null \
            || die "dump failed verification - pg_restore could not read $target"
    fi

    printf '%s\n' "$target"
    info "OK: $(wc -c < "$target") bytes, table of contents verified, records rows=${rows:-?}"
    info "restore it with: ./restore.sh $target"
}

main "$@"
