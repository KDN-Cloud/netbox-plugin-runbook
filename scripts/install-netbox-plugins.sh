#!/bin/bash

echo "**** Installing NetBox plugins ****"

docker exec -u root NETBOX /lsiopy/bin/pip install \
  netbox-topology-views \
  netbox-acls \
  netbox-qrcode \
  netbox-floorplan-plugin \
  netbox-attachments \
  netbox-lists \
  netbox-lifecycle \
  netbox-documents \
  slurpit_netbox \
  netbox-secrets \
  netbox-reorder-rack \
  netbox-ipcalculator \
  netbox-security \
  netbox-plugin-dns

sleep 20

echo "**** Running NetBox plugin migrations ****"
docker exec NETBOX bash -c "cd /app/netbox/netbox && python manage.py migrate --no-input"
docker exec NETBOX bash -c "cd /app/netbox/netbox && python manage.py collectstatic --no-input --clear"
echo "**** Restarting NetBox ****"
cd ~/netbox && docker compose restart netbox
echo "**** Done ****"
