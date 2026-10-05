#!/bin/sh
# Hourly housekeeping: expire reservations, send reminders, retry emails.
# A full data backup is written once a day (at BACKUP_HOUR, default 02:00).
BACKUP_HOUR="${BACKUP_HOUR:-02}"
last_backup_day=""
while true; do
  python manage.py run_scheduled_tasks || echo "scheduled tasks failed; will retry next hour"
  today="$(date +%Y-%m-%d)"
  if [ "$(date +%H)" = "$BACKUP_HOUR" ] && [ "$today" != "$last_backup_day" ]; then
    python manage.py backup --with-media --keep 30 && last_backup_day="$today"
  fi
  sleep 3600
done
