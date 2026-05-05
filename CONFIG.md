# configuration.py

The very bottom of the file `configuration.py` should contain the following blocks.
The file lives at `~/netbox/config/configuration.py` on the host.

---

## API Token Peppers

Generate a pepper key with:

```bash
openssl rand -base64 60
```

This gives ~80 characters, well above the 50 character minimum. Add it to `configuration.py`:

```python
API_TOKEN_PEPPERS = {
    1: 'your-generated-key-here',
}
```

> ⚠️ If you regenerate this key, all existing API tokens will be invalidated.

---

## Plugins

```python
# Enable Plugins
PLUGINS = [
    'netbox_topology_views',
    'netbox_acls',
    'netbox_qrcode',
    'netbox_floorplan',
    'netbox_attachments',
    'netbox_lists',
    'netbox_lifecycle',
    'netbox_documents',
    'slurpit_netbox',
    'netbox_secrets',
    'netbox_reorder_rack',
    'netbox_ipcalculator',
    'netbox_security',
    'netbox_dns',
]

PLUGINS_CONFIG = {
}
```

> ⚠️ The module name in `PLUGINS` is not always the same as the pip package name.
> For example `netbox-floorplan-plugin` installs as `netbox_floorplan` (not `netbox_floorplan_plugin`).
> Always verify with:
> ```bash
> docker exec NETBOX find /lsiopy/lib/python3.12/site-packages -type d | grep -i <plugin>
> ```

---

## Plugin Auto-Installer (systemd watcher)

A systemd service watches for NETBOX container start events and automatically
reinstalls all plugins after image updates via Dockhand.

### Service file
`/etc/systemd/system/netbox-plugin-watcher.service`

### Watcher script
`~/netbox/config/scripts/netbox-plugin-watcher.sh`

### Install script
`~/netbox/config/scripts/install-netbox-plugins.sh`

### Log file
`/var/log/netbox/plugin-watcher.log`

### Service management

```bash
# Check status
sudo systemctl status netbox-plugin-watcher

# View live logs
sudo journalctl -u netbox-plugin-watcher -f

# Restart service
sudo systemctl restart netbox-plugin-watcher

# View log file
tail -f /var/log/netbox/plugin-watcher.log
```

### Log file permissions

The log directory and file must be owned by the `docker` user:

```bash
sudo chown docker:docker /var/log/netbox/
sudo chown docker:docker /var/log/netbox/plugin-watcher.log
```

---

## Adding a New Plugin

1. Add pip package to `~/netbox/config/scripts/install-netbox-plugins.sh`
2. Add module name to `PLUGINS = [...]` in `configuration.py`
3. Run the install script (plugins must be installed BEFORE NetBox restarts):

```bash
~/netbox/config/scripts/install-netbox-plugins.sh
```

> See `README.md` for full plugin management workflow and troubleshooting.
