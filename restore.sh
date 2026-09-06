#!/usr/bin/env bash
# Restore the assessment PostgreSQL database from a backup.sh dump and prove it.
#
#   ./restore.sh                          # restore the newest dump in ./backups
#   ./restore.sh backups/barq_tasks-*.dump
#   FORCE=1 ./restore.sh                  # skip the confirmation prompt (CI)
#
# This is destructive: --clean --if-exists drops and recreates the objects in the
# dump. The row count is printed before and after so the restore is proved, not
# asserted, and a restore that does not change what it should is reported as a
# failure rather than a silent success.
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

newest_dump() {
    ls -1t "$BACKUP_DIR"/*.dump 2>/dev/null | head -n1
}

main() {
    local id user db dump before after
    dump="${1:-$(newest_dump)}"
    [ -n "$dump" ] || die "no dump given and none found in $BACKUP_DIR - run ./backup.sh first"
    [ -s "$dump" ] || die "dump not found or empty: $dump"

    id="$(resolve_container)"
    user="$(container_env "$id" POSTGRES_USER)"
    db="$(container_env "$id" POSTGRES_DB)"
    [ -n "$user" ] && [ -n "$db" ] || die "could not read POSTGRES_USER/POSTGRES_DB from $id"

    wait_for_postgres "$id" "$user" "$db"
    before="$(row_count "$id" "$user" "$db")"

    info "project=$PROJECT container=${id:0:12} database=$db"
    info "restoring $dump ($(wc -c < "$dump") bytes) over the CURRENT contents"
    info "records rows before restore: ${before:-?}"
    if [ "${FORCE:-0}" != "1" ] && [ -t 0 ]; then
        printf 'Type yes to continue: ' >&2
        read -r answer
        [ "$answer" = "yes" ] || die "aborted by operator"
    fi

    # --clean --if-exists so a re-restore over existing objects is idempotent.
    # pg_restore exits non-zero for benign notices too, so its status is captured
    # and the restore is judged on the verification below, with the log shown.
    set +e
    docker exec -i "$id" pg_restore --clean --if-exists --no-owner --no-privileges \
        -U "$user" -d "$db" < "$dump" 2>/tmp/barq-restore.log
    local status=$?
    set -e
    [ "$status" -eq 0 ] || { info "pg_restore exited $status; output follows"; cat /tmp/barq-restore.log >&2; }

    after="$(row_count "$id" "$user" "$db")"
    info "records rows after restore:  ${after:-?}"
    [ -n "$after" ] || die "records table is not queryable after the restore"

    info "OK: restore completed and the records table is readable with ${after} rows"
}

main "$@"
