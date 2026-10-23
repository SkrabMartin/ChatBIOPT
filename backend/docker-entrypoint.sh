#!/bin/sh
set -eu

# Apply pending migrations before serving. Set RUN_MIGRATIONS=0 to manage them out of band.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    alembic upgrade head
fi

exec "$@"
