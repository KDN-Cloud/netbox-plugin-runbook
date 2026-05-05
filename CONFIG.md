# configuration.py

The very bottom of the file `configuration.py` should contain the list of plugins.

_Note_: Generate API key with `openssl rand -base64 60` which gives ~80 character key, well above the 50 character minimum.

```
API_TOKEN_PEPPERS = {
    1: '7ew8C3L4236l2m8VPT31rKha0QhhVpu39qE7l8zc8ASMgGIM2Wmv3+ejZO+1Z+NEUi9qbe+85R7QIBN0',
}

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
