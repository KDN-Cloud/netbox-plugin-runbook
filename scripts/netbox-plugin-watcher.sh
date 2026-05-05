#!/bin/bash
# Watches for NETBOX container restart/start events and reinstalls plugins

LOGFILE=/var/log/netbox/plugin-watcher.log

docker events \
  --filter "container=NETBOX" \
  --filter "event=start" \
  --format '{{.Action}}' | while read event; do
    echo "[$(date)] NETBOX started - waiting 60s then reinstalling plugins..." | tee -a $LOGFILE
    sleep 60
    /home/docker/netbox/config/scripts/install-netbox-plugins.sh >> $LOGFILE 2>&1
    echo "[$(date)] Plugin reinstall complete" | tee -a $LOGFILE
done
