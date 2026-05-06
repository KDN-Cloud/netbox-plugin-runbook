#!/bin/bash
# Transfer the backup file to the PostgreSQL container
docker cp /home/docker/netbox/backup-netbox-latest.sql NETBOX-POSTGRES-DB:/tmp/backup-netbox-latest.sql

# Access the PostgreSQL container and execute database commands
docker exec -it NETBOX-POSTGRES-DB bash -c "
  # Drop the existing netbox database
  psql -U netbox-user -c 'DROP DATABASE IF EXISTS netbox;'

  # Create a new netbox database
  psql -U netbox-user -c 'CREATE DATABASE netbox;'

  # Restore the backup into the netbox database
  psql -U netbox-user -d netbox < /tmp/backup-netbox-latest.sql

  # Clean up the backup file inside the container
  rm /tmp/*.sql
"

# Restart the NetBox service to apply changes
docker-compose -f /home/docker/netbox/docker-compose.yml restart netbox
