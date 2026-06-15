#!/usr/bin/env bash
set -euo pipefail

# Soft-delete 4ga Boards test accounts so registration tests can reuse emails.
# Default mode is dry-run. Pass --yes to apply changes.

APP_DIR="${FOURGA_BOARDS_DIR:-/home/robot/桌面/4gaBoards-main}"
DB_NAME="${FOURGA_DB_NAME:-4gaBoards}"
DB_USER="${FOURGA_DB_USER:-postgres}"
MODE="dry-run"
CONNECT_MODE="auto"

EMAILS=(
  "testuser001@test.com"
  "webagent_test@example.com"
)
USERNAMES=(
  "testuser001"
  "webagent_user"
)
EMAIL_LIKES=(
  "testuser%@test.com"
  "webagent_%@example.com"
)

usage() {
  cat <<'USAGE'
Usage:
  scripts/cleanup_4gaboards_test_accounts.sh [options]

Options:
  --yes                 Apply cleanup. Without this flag the script only lists matches.
  --dry-run             Only list matching active accounts. This is the default.
  --email EMAIL         Add exact email to clean. Can be repeated.
  --username USERNAME   Add exact username to clean. Can be repeated.
  --like PATTERN        Add SQL ILIKE email pattern. Can be repeated.
  --only EMAIL          Clear defaults and clean only this exact email.
  --docker              Force docker compose connection.
  --local               Force local psql connection using DATABASE_URL/server/.env.
  --app-dir PATH        4gaBoards project directory. Default: /home/robot/桌面/4gaBoards-main
  -h, --help            Show this help.

Examples:
  scripts/cleanup_4gaboards_test_accounts.sh
  scripts/cleanup_4gaboards_test_accounts.sh --yes
  scripts/cleanup_4gaboards_test_accounts.sh --only testuser001@test.com --yes
  scripts/cleanup_4gaboards_test_accounts.sh --like 'testuser%@test.com' --yes

The script soft-deletes rows in user_account by setting deleted_at=now().
It also expires active sessions for those users.
USAGE
}

sql_quote() {
  local value="$1"
  value="${value//\'/\'\'}"
  printf "'%s'" "$value"
}

reset_defaults() {
  EMAILS=()
  USERNAMES=()
  EMAIL_LIKES=()
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)
      MODE="apply"
      shift
      ;;
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --email)
      EMAILS+=("${2:?--email requires a value}")
      shift 2
      ;;
    --username)
      USERNAMES+=("${2:?--username requires a value}")
      shift 2
      ;;
    --like)
      EMAIL_LIKES+=("${2:?--like requires a value}")
      shift 2
      ;;
    --only)
      reset_defaults
      EMAILS+=("${2:?--only requires an email}")
      shift 2
      ;;
    --docker)
      CONNECT_MODE="docker"
      shift
      ;;
    --local)
      CONNECT_MODE="local"
      shift
      ;;
    --app-dir)
      APP_DIR="${2:?--app-dir requires a path}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

build_condition() {
  local conditions=()
  local item

  for item in "${EMAILS[@]}"; do
    conditions+=("lower(email) = lower($(sql_quote "$item"))")
  done
  for item in "${USERNAMES[@]}"; do
    conditions+=("lower(username) = lower($(sql_quote "$item"))")
  done
  for item in "${EMAIL_LIKES[@]}"; do
    conditions+=("email ILIKE $(sql_quote "$item")")
  done

  if [[ ${#conditions[@]} -eq 0 ]]; then
    echo "No account filters configured." >&2
    exit 2
  fi

  local joined="${conditions[0]}"
  for item in "${conditions[@]:1}"; do
    joined+=" OR $item"
  done
  printf '%s' "$joined"
}

CONDITION="$(build_condition)"

read_database_url() {
  if [[ -n "${DATABASE_URL:-}" ]]; then
    printf '%s' "$DATABASE_URL"
    return
  fi

  local env_file="$APP_DIR/server/.env"
  if [[ -f "$env_file" ]]; then
    grep -E '^DATABASE_URL=' "$env_file" | tail -n 1 | cut -d= -f2-
    return
  fi
}

join_lines() {
  local item
  for item in "$@"; do
    printf '%s\n' "$item"
  done
}

find_pg_module() {
  local package_json
  package_json="$(find "$APP_DIR/node_modules/.pnpm" -path '*/node_modules/pg/package.json' -print -quit 2>/dev/null || true)"
  if [[ -n "$package_json" ]]; then
    dirname "$package_json"
    return
  fi
  if [[ -d "$APP_DIR/node_modules/pg" ]]; then
    printf '%s\n' "$APP_DIR/node_modules/pg"
  fi
}

run_node_cleanup() {
  local db_url="$1"
  local pg_module="$2"
  CLEANUP_MODE="$MODE" \
  CLEANUP_DATABASE_URL="$db_url" \
  CLEANUP_PG_MODULE="$pg_module" \
  CLEANUP_EMAILS="$(join_lines "${EMAILS[@]}")" \
  CLEANUP_USERNAMES="$(join_lines "${USERNAMES[@]}")" \
  CLEANUP_EMAIL_LIKES="$(join_lines "${EMAIL_LIKES[@]}")" \
  node <<'NODE'
const { Client } = require(process.env.CLEANUP_PG_MODULE);

const splitLines = (value) => (value || '').split('\n').map((item) => item.trim()).filter(Boolean);
const mode = process.env.CLEANUP_MODE || 'dry-run';
const databaseUrl = process.env.CLEANUP_DATABASE_URL;
const emails = splitLines(process.env.CLEANUP_EMAILS);
const usernames = splitLines(process.env.CLEANUP_USERNAMES);
const likes = splitLines(process.env.CLEANUP_EMAIL_LIKES);

const clauses = [];
const params = [];
const addParam = (value) => {
  params.push(value);
  return `$${params.length}`;
};

for (const email of emails) {
  clauses.push(`lower(email) = lower(${addParam(email)})`);
}
for (const username of usernames) {
  clauses.push(`lower(username) = lower(${addParam(username)})`);
}
for (const pattern of likes) {
  clauses.push(`email ILIKE ${addParam(pattern)}`);
}

if (!databaseUrl) {
  console.error('DATABASE_URL not found.');
  process.exit(1);
}
if (clauses.length === 0) {
  console.error('No account filters configured.');
  process.exit(2);
}

const where = clauses.join(' OR ');
const client = new Client({ connectionString: databaseUrl });

async function main() {
  await client.connect();

  if (mode === 'dry-run') {
    const result = await client.query(
      `SELECT id, email, username, name, created_at
       FROM user_account
       WHERE deleted_at IS NULL AND (${where})
       ORDER BY created_at DESC NULLS LAST`,
      params,
    );
    console.log('Matching active 4ga Boards test accounts:');
    if (result.rows.length === 0) {
      console.log('(none)');
    } else {
      console.table(result.rows);
    }
    console.log('\nDry-run only. Re-run with --yes to soft-delete these accounts.');
    return;
  }

  await client.query('BEGIN');
  try {
    const target = await client.query(
      `SELECT id, email, username
       FROM user_account
       WHERE deleted_at IS NULL AND (${where})
       FOR UPDATE`,
      params,
    );
    const ids = target.rows.map((row) => row.id);

    let expiredSessions = 0;
    let softDeletedUsers = 0;
    if (ids.length > 0) {
      const sessionResult = await client.query(
        `UPDATE session
         SET deleted_at = now(), updated_at = now()
         WHERE deleted_at IS NULL AND user_id = ANY($1::bigint[])
         RETURNING user_id`,
        [ids],
      );
      expiredSessions = sessionResult.rowCount;

      const userResult = await client.query(
        `UPDATE user_account
         SET deleted_at = now(), updated_at = now()
         WHERE id = ANY($1::bigint[])
         RETURNING id, email, username`,
        [ids],
      );
      softDeletedUsers = userResult.rowCount;
    }

    await client.query('COMMIT');
    console.log({ soft_deleted_users: softDeletedUsers, expired_sessions: expiredSessions });

    const remaining = await client.query(
      `SELECT id, email, username, name, created_at
       FROM user_account
       WHERE deleted_at IS NULL AND (${where})
       ORDER BY created_at DESC NULLS LAST`,
      params,
    );
    console.log('Remaining active matching accounts after cleanup:');
    if (remaining.rows.length === 0) {
      console.log('(none)');
    } else {
      console.table(remaining.rows);
    }
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  }
}

main()
  .catch((error) => {
    console.error(error);
    process.exit(1);
  })
  .finally(async () => {
    await client.end().catch(() => {});
  });
NODE
}

psql_cmd=()

docker_db_container() {
  docker compose -f "$APP_DIR/docker-compose.yml" ps -q db 2>/dev/null || true
}

if [[ "$CONNECT_MODE" == "docker" || "$CONNECT_MODE" == "auto" ]]; then
  container_id="$(docker_db_container)"
  if [[ -n "$container_id" ]]; then
    psql_cmd=(docker compose -f "$APP_DIR/docker-compose.yml" exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME")
  elif [[ "$CONNECT_MODE" == "docker" ]]; then
    echo "Could not find running docker compose service 'db' in $APP_DIR." >&2
    exit 1
  fi
fi

if [[ ${#psql_cmd[@]} -eq 0 ]]; then
  db_url="$(read_database_url)"
  if [[ -z "${db_url:-}" ]]; then
    echo "DATABASE_URL not found. Set DATABASE_URL or use --docker with a running compose stack." >&2
    exit 1
  fi
  if command -v psql >/dev/null 2>&1; then
    psql_cmd=(psql -v ON_ERROR_STOP=1 "$db_url")
  else
    pg_module="$(find_pg_module)"
    if [[ -z "$pg_module" ]]; then
      echo "Neither psql nor the Node pg module was found. Install psql or run pnpm install in $APP_DIR." >&2
      exit 1
    fi
    run_node_cleanup "$db_url" "$pg_module"
    exit 0
  fi
fi

if [[ "$MODE" == "dry-run" ]]; then
  cat <<SQL | "${psql_cmd[@]}"
\pset pager off
\echo 'Matching active 4ga Boards test accounts:'
SELECT id, email, username, name, created_at
FROM user_account
WHERE deleted_at IS NULL
  AND ($CONDITION)
ORDER BY created_at DESC NULLS LAST;
SQL
  echo
  echo "Dry-run only. Re-run with --yes to soft-delete these accounts."
  exit 0
fi

cat <<SQL | "${psql_cmd[@]}"
\pset pager off
BEGIN;

WITH target AS (
  SELECT id, email, username
  FROM user_account
  WHERE deleted_at IS NULL
    AND ($CONDITION)
  FOR UPDATE
),
expired_sessions AS (
  UPDATE session
  SET deleted_at = now(),
      updated_at = now()
  WHERE deleted_at IS NULL
    AND user_id IN (SELECT id FROM target)
  RETURNING user_id
),
deleted_users AS (
  UPDATE user_account
  SET deleted_at = now(),
      updated_at = now()
  WHERE id IN (SELECT id FROM target)
  RETURNING id, email, username
)
SELECT
  (SELECT count(*) FROM deleted_users) AS soft_deleted_users,
  (SELECT count(*) FROM expired_sessions) AS expired_sessions;

COMMIT;

\echo 'Remaining active matching accounts after cleanup:'
SELECT id, email, username, name, created_at
FROM user_account
WHERE deleted_at IS NULL
  AND ($CONDITION)
ORDER BY created_at DESC NULLS LAST;
SQL
