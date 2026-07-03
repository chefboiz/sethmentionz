#!/usr/bin/env bash
# Run all pending migrations against the sethmentionz database.
# Usage:  bash db/migrate.sh
# Requires DATABASE_URL to be set (or loaded from .env in the current shell).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS_DIR="$SCRIPT_DIR/migrations"

if [ -z "${DATABASE_URL:-}" ]; then
  if [ -f "$SCRIPT_DIR/../.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/../.env" | xargs)
  fi
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL is not set" >&2
  exit 1
fi

echo "Running migrations against: ${DATABASE_URL##*@}"

for f in "$MIGRATIONS_DIR"/*.sql; do
  echo "  → $(basename "$f")"
  psql "$DATABASE_URL" -f "$f"
done

echo "All migrations applied."
