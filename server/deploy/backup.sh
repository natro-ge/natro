#!/bin/sh
# Nightly copy of what's his and can't be got back (memory, notes, reminders, routines, watchers, places and cards),
# run by cron as the natro user:
#   30 3 * * * /home/natro/natro/server/deploy/backup.sh
# Keeps 14 days in ~/backups. (Syncthing also keeps both folders on the PC.)
set -e
cd "$HOME/natro"
mkdir -p "$HOME/backups"
# Only what's there: tar fails on what isn't.
set -- memory notes
for kept in reminders.json routines.json watchers.json places.json place_actions.json cards; do
    if [ -e "$kept" ]; then
        set -- "$@" "$kept"
    fi
done
tar czf "$HOME/backups/natro-$(date +%F).tar.gz" --exclude=.stversions "$@"
find "$HOME/backups" -name 'natro-*.tar.gz' -mtime +14 -delete
