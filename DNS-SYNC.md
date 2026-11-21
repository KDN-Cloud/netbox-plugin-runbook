# NetBox DNS refresh

Docker-host cron runs [the refresh script](scripts/dns-sync/run-dns-refresh.sh) every four hours. The script reads configured hostnames from Nginx Proxy Manager and passes them to [the updater](scripts/dns-sync/dns_refresh.py) in the NetBox container. The updater checks active zones in the default view, then verifies known records and configured hostnames against public authoritative DNS or the lab resolver.

NetBox's **DNS hosts** dashboard tile and directory read those records directly. A job that makes changes records them in NetBox's change log under **Scheduled DNS sync**. Repeated authoritative absence is required before a public record is marked inactive. Provider records, local resolver settings, and retired zones stay outside the write scope.

The updater uses live DNS and Nginx Proxy Manager's configuration. It does not pull a Git repository at runtime. Keep bulk-import snapshots in the [DNS data repo](https://git.kf7.dev/ak/netbox-dns-data). Discovering arbitrary new records and reading Cloudflare's hidden proxy origins requires access to the provider's inventory. Discovering new local overrides outside the proxy configuration requires a source that can enumerate them.

## Production schedule

The active cron entry on the Docker host runs at minute 7 every four hours. It invokes the installed copy under the persistent NetBox config mount.

```cron
7 */4 * * * umask 077; /home/docker/netbox/config/dns-sync/run-dns-refresh.sh --apply > /home/docker/netbox/config/dns-sync/cron-last-run.log 2>&1 # netbox-dns-refresh
```

The Docker host needs Bash, Python 3, Docker, `flock`, and `timeout`. The runner expects the existing `NETBOX` and `npm-app` containers. NetBox must have NetBox DNS installed and its persistent config mounted at `/config`.

## Preview and updates

From a checkout on the Docker host, install only the source files into the persistent config directory. This preserves the scheduler, logs, and recorded absence observations.

```bash
target=/home/docker/netbox/config/dns-sync
install -d -m 700 "$target"
flock -w 30 "$target/refresh.lock" bash -eu -c '
  install -m 600 scripts/dns-sync/dns_refresh.py "$1/dns_refresh.py"
  install -m 700 scripts/dns-sync/run-dns-refresh.sh "$1/run-dns-refresh.sh"
' _ "$target"
"$target/run-dns-refresh.sh" --preview
```

The preview validates model changes in a transaction that rolls back. Inspect `cron-last-run.log` for the last scheduled summary. See `unavailable_zones` for zones whose authority could not be verified. The runner uses a lock to avoid concurrent refreshes. The observation file, lock, and logs are runtime files.
