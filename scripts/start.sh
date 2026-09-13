#!/bin/sh
# Container entrypoint.
#
# The bot used to start with `alembic upgrade head && exec python bot.py`, so any
# database hiccup at container start (an RDS restart, a connection pooler that is
# not up yet, a rotated credential) exited before the bot ever ran. With
# `restart: unless-stopped` that becomes a silent, permanent crash loop, and it
# takes down every command — including the many that never touch the database.
#
# So migrations are retried with backoff, and a persistent failure is loud but
# does not stop the bot from connecting to Discord. Set REQUIRE_MIGRATIONS=1 to
# get the old fail-fast behaviour instead.

set -eu

attempts=${MIGRATION_MAX_ATTEMPTS:-5}
delay=${MIGRATION_RETRY_DELAY:-2}
attempt=1

while : ; do
  echo "Running database migrations (attempt ${attempt}/${attempts})..."
  if alembic upgrade head; then
    echo "Database migrations applied."
    break
  fi

  if [ "${attempt}" -ge "${attempts}" ]; then
    echo "ERROR: database migrations failed after ${attempts} attempts." >&2
    if [ "${REQUIRE_MIGRATIONS:-0}" != "0" ]; then
      echo "REQUIRE_MIGRATIONS is set; refusing to start the bot." >&2
      exit 1
    fi
    echo "WARNING: starting the bot anyway so commands that do not need the" >&2
    echo "database keep working. Database-backed commands will fail until the" >&2
    echo "database is reachable and migrations are applied." >&2
    break
  fi

  echo "Migrations failed; retrying in ${delay}s..." >&2
  sleep "${delay}"
  attempt=$((attempt + 1))
  delay=$((delay * 2))
done

echo "Starting the Discord bot..."
exec python bot.py
