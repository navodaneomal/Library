#!/bin/sh
# Applies database migrations on start-up (safe to run repeatedly), then
# runs the given command. Set RUN_MIGRATIONS=0 for extra worker containers.
set -e
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  python manage.py migrate --noinput
fi
exec "$@"
