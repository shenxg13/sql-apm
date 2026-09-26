#!/usr/bin/env bash
# PostgreSQL 17 project bootstrap and verified schema installation.
set -euo pipefail
usage() {
    cat <<'EOF'
Usage: scripts/db/initialize.sh {all|bootstrap|schema|check}
  --host HOST_OR_SOCKET --port PORT
  [--database sql_apm] [--schema sql_apm] [--role sql_apm]
  [--admin-user USER --admin-database DATABASE] [--pg-bin DIRECTORY]

Connections always name host, port, database and user explicitly.
bootstrap/all require both admin options. schema/check use the project role.
Use a protected PGPASSFILE or configured local authentication; no password flags.
bootstrap creates a LOGIN role without a password. If password authentication
is required, set it using administrator psql \password, then run schema.
EOF
}
die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}
mode=${1:-}
[[ $# -gt 0 ]] && shift
case "$mode" in all | bootstrap | schema | check) ;; -h | --help)
    usage
    exit 0
    ;;
*)
    usage >&2
    exit 2
    ;;
esac
host='' port='' admin_user='' admin_database='' pg_bin=''
database=sql_apm schema=sql_apm project_role=sql_apm
while (($#)); do
    [[ $# -ge 2 ]] || die "missing value for $1"
    case "$1" in
        --host) host=$2 ;; --port) port=$2 ;;
        --database) database=$2 ;; --schema) schema=$2 ;; --role) project_role=$2 ;;
        --admin-user) admin_user=$2 ;; --admin-database) admin_database=$2 ;;
        --pg-bin) pg_bin=$2 ;; *) die "unknown option: $1" ;;
    esac
    shift 2
done
[[ -n $host && $port =~ ^[0-9]+$ ]] || die 'explicit --host and numeric --port required'
((10#$port >= 1 && 10#$port <= 65535)) || die 'invalid port'
for name in "$database" "$schema" "$project_role"; do
    [[ $name =~ ^[a-z][a-z0-9_]{0,62}$ && $name != pg_* ]] || die 'project names must match [a-z][a-z0-9_]{0,62}, excluding pg_*'
    case "$name" in postgres | template0 | template1 | public | information_schema) die "reserved project name: $name" ;; esac
done
if [[ $mode == all || $mode == bootstrap ]]; then
    [[ -n $admin_user && $admin_database =~ ^[a-z][a-z0-9_]{0,62}$ && $admin_user != "$project_role" ]] || die 'explicit, distinct administrator identity and database required'
fi
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
psql=psql
[[ -z $pg_bin ]] || psql="$pg_bin/psql"
command -v "$psql" >/dev/null || die 'psql not found'
# -X ignores psqlrc; PGOPTIONS is not inherited into administrative DDL sessions.
unset PGOPTIONS PGSERVICE PGSERVICEFILE PGHOSTADDR
export PGCONNECT_TIMEOUT=10
common=(-X -w -q --host="$host" --port="$port" --set=ON_ERROR_STOP=1
    --set=project_database="$database" --set=project_schema="$schema" --set=project_role="$project_role")
phase=preflight
trap 'printf "ERROR: initialization failed at phase=%s (completed stages retained; see recovery guide)\n" "$phase" >&2' ERR
if [[ $mode == all || $mode == bootstrap ]]; then
    phase=bootstrap
    "$psql" "${common[@]}" --username="$admin_user" --dbname="$admin_database" --file="$root/sql_apm/storage/bootstrap.sql"
fi
if [[ $mode != bootstrap ]]; then
    phase=schema
    check_only=false
    [[ $mode != check ]] || check_only=true
    script_sha256=$(sha256sum "$root/sql_apm/storage/schema.sql")
    script_sha256=${script_sha256%% *}
    "$psql" "${common[@]}" --username="$project_role" --dbname="$database" \
        --set=script_sha256="$script_sha256" --set=check_only="$check_only" \
        --file="$root/sql_apm/storage/initialize.sql"
fi
printf 'OK: mode=%s database=%s schema=%s role=%s\n' "$mode" "$database" "$schema" "$project_role"
