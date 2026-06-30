# NetBox Plugin Management (LSIO Docker)

## ⚠️ Critical Rule

**Plugins are installed inside the container and do NOT persist across container recreates.**  
Always run the install script BEFORE restarting NetBox, never after.

---

## Adding a New Plugin

### Step 1 — Find the correct pip package name and module name

These are often different. Check the plugin's GitHub README for both:

| pip package name | PLUGINS module name |
|---|---|
| `netbox-topology-views` | `netbox_topology_views` |
| `netbox-floorplan-plugin` | `netbox_floorplan` ← different! |
| `netbox-plugin-dns` | `netbox_dns` |

To verify the module name if unsure:
```bash
docker exec NETBOX find /lsiopy/lib/python3.12/site-packages -type d | grep -i <plugin-name>
```

---

### Step 2 — Add the pip package to the install script

```bash
nano ~/netbox/config/scripts/install-netbox-plugins.sh
```

Add the new package to the pip install list.

---

### Step 3 — Add the module name to configuration.py

```bash
nano ~/netbox/config/configuration.py
```

Add to the `PLUGINS = [...]` list using underscores (the module name, not the pip name).

---

### Step 4 — Run the install script FIRST

```bash
~/netbox/config/scripts/install-netbox-plugins.sh
```

Wait for it to complete fully. The script will:
1. Install all pip packages into the container
2. Run database migrations
3. Collect static files
4. Restart NetBox automatically

---

## After a Container Recreate / Image Update

Dockhand or a manual `docker compose up -d` will recreate the container and wipe all installed plugins. Run the install script immediately after:

```bash
~/netbox/config/scripts/install-netbox-plugins.sh
```

---

## Current Plugin List

| pip package | module name |
|---|---|
| `netbox-topology-views` | `netbox_topology_views` |
| `netbox-acls` | `netbox_acls` |
| `netbox-qrcode` | `netbox_qrcode` |
| `netbox-floorplan-plugin` | `netbox_floorplan` |
| `netbox-attachments` | `netbox_attachments` |
| `netbox-lists` | `netbox_lists` |
| `netbox-lifecycle` | `netbox_lifecycle` |
| `netbox-documents` | `netbox_documents` |
| `slurpit_netbox` | `slurpit_netbox` |
| `netbox-secrets` | `netbox_secrets` |
| `netbox-reorder-rack` | `netbox_reorder_rack` |
| `netbox-ipcalculator` | `netbox_ipcalculator` |
| `netbox-security` | `netbox_security` |
| `netbox-plugin-dns` | `netbox_dns` |

---

## Device Type Library Sync (Automated)

Unlike plugins, device type definitions are synced via a standalone `docker run` invocation triggered by cron, **not** part of the docker-compose stack. This is intentional: a one-shot container inside compose causes Dockhand to flag the stack as "partial" for the duration of the run, even with `restart: "no"` or `restart: on-failure:N`. Running it fully standalone via cron sidesteps that entirely.

### Cron entry

```
0 3 * * * /bin/bash /home/docker/netbox/config/scripts/netbox_sync.sh >> /home/docker/netbox/config/scripts/netbox_sync.log 2>&1
```

### Script

See [`scripts/netbox_sync.sh`](scripts/netbox_sync.sh):

```bash
#!/bin/bash
docker run --rm \
  --network netbox_default \
  --name netbox-dt-import-cron \
  -e NETBOX_URL=http://netbox:8000 \
  -e NETBOX_TOKEN="${NETBOX_TOKEN}" \
  -e REPO_URL=https://github.com/netbox-community/devicetype-library.git \
  ghcr.io/marcinpsk/device-type-library-import:latest \
  python nb-dt-import.py --vendors 'Apple,APC,ASUS,Cable Matters,CyberPower,Intel,Lenovo,Netgate,NVIDIA,Raspberry Pi Foundation,Seagate,Server Technology,Synology,TP-Link,Ubiquiti,WatchGuard' --update
```

Pulls the community devicetype-library and imports/updates only the vendors relevant to this homelab, rather than the full catalog. `NETBOX_TOKEN` should be exported in the cron user's environment or sourced from a separate env file. Never commit the literal token.

### Why not run this inside docker-compose.yml?

A one-shot service still gets rolled into Dockhand's stack health check while it's mid-execution, surfacing a false "partial" status until the container exits. Keeping the sync fully outside compose means Dockhand only ever monitors persistent services, and the import container never touches that state.

### Manually triggering a one-off device type import

To import the full upstream library (not just the vendor-filtered list above):

```bash
docker run --rm \
  --network netbox_default \
  -e NETBOX_URL=http://netbox:8000 \
  -e NETBOX_TOKEN="${NETBOX_TOKEN}" \
  ghcr.io/marcinpsk/device-type-library-import:latest
```

This runs the exact same import logic as the cron job, minus the `--vendors` filter and `--update` flag.

---

## Troubleshooting

**NetBox crashes with `ModuleNotFoundError` on startup:**  
The plugin listed in `PLUGINS` isn't installed in the container. Run the install script, then restart.

**Plugin is installed but wrong module name in PLUGINS:**  
Find the real module name:
```bash
docker exec NETBOX find /lsiopy/lib/python3.12/site-packages -type d | grep -i <plugin>
```

**Emergency recovery — NetBox won't start:**  
Set `PLUGINS = []` in `configuration.py`, restart NetBox to get it healthy, then run the install script and re-add plugins.

```bash
# 1. Empty plugins
nano ~/netbox/config/configuration.py  # set PLUGINS = []

# 2. Restart to get healthy
docker compose restart netbox

# 3. Run install script
~/netbox/config/scripts/install-netbox-plugins.sh
```

**NetBox crash-loops after an image update, unrelated to plugins (e.g. missing `core_job.notifications` column):**  
This is a core migration problem, not a plugin or device-type problem. A new image version shipped a migration (e.g. `core.0024_job_notifications`) that didn't apply cleanly, sometimes because a plugin migration ran out of order relative to its core dependency.

1. **Always back up Postgres before pulling a new NetBox image:**
   ```bash
   docker exec NETBOX-POSTGRES-DB pg_dump -U netbox-user netbox > netbox_backup_$(date +%F).sql
   ```
2. Check migration state before letting uWSGI serve traffic:
   ```bash
   docker exec NETBOX python /opt/netbox/netbox/manage.py migrate --check
   ```
3. If a migration is stuck or a column is missing, inspect `django_migrations` directly via `psql` and reconcile manually (insert the missing migration record and/or `ALTER TABLE` the missing column) rather than blindly re-running `migrate`.
4. Pin plugin versions to the NetBox release you're running. All plugins in the table above currently cap compatibility at older NetBox minor versions and will be silently skipped (not erroring) if the core version moves past what they declare support for.

---

## install-netbox-plugins.sh Location

```
~/netbox/config/scripts/install-netbox-plugins.sh
```

## Related
[CONFIG.md](CONFIG.md)

