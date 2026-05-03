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

---

## install-netbox-plugins.sh Location

```
~/netbox/config/scripts/install-netbox-plugins.sh
```
