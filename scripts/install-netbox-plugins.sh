#!/bin/bash

echo "**** Installing NetBox plugins ****"

# Incompatible with NetBox 4.6.0 - commented out:
# netbox-acls
# netbox-qrcode
# netbox-floorplan-plugin
# netbox-attachments
# netbox-secrets

docker exec -u root NETBOX /lsiopy/bin/pip install \
  netbox-topology-views \
  netbox-lists \
  netbox-lifecycle \
  netbox-documents \
  slurpit_netbox \
  netbox-reorder-rack \
  netbox-ipcalculator \
  netbox-security \
  netbox-plugin-dns

sleep 20

echo "**** Running NetBox plugin migrations ****"
docker exec NETBOX bash -c "cd /app/netbox/netbox && python manage.py migrate --no-input"
docker exec NETBOX bash -c "cd /app/netbox/netbox && python manage.py collectstatic --no-input --clear"
#echo "**** Restarting NetBox ****"
#cd ~/netbox && docker compose restart netbox
echo "**** Done ****"
