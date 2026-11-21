# NetBox DNS refresh

Docker-host cron runs [the refresh script](scripts/dns-sync/run-dns-refresh.sh) every four hours. The script reads configured hostnames from Nginx Proxy Manager and passes them to [the updater](scripts/dns-sync/dns_refresh.py) in the NetBox container. The updater checks active zones in the default view, then verifies known records and configured hostnames against public authoritative DNS or the lab resolver.

NetBox's **DNS hosts** dashboard tile and directory read those records directly. A job that makes changes records them in NetBox's change log under **Scheduled DNS sync**. Repeated authoritative absence is required before a public record is marked inactive. Provider records, local resolver settings, and retired zones stay outside the write scope.

The updater reads live DNS, Nginx Proxy Manager's configuration, and Cloudflare, Name.com, and pfSense inventories when credentials are configured. Keep bulk-import snapshots in the [DNS data repo](https://git.kf7.dev/ak/netbox-dns-data).

## Cloudflare API access

Create a dedicated custom token with **Zone → Zone → Read** and **Zone → DNS → Read**. Include only the Cloudflare zones managed in NetBox. The first permission resolves zone IDs and nameservers. The second lists DNS records and configured proxy origins. See the [Cloudflare token setup](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/).

On the Docker host, store the token alone in `/home/docker/netbox/config/dns-sync/cloudflare.token`, owned by the Docker operator with mode `0600`. NetBox reads it as `/config/dns-sync/cloudflare.token`. Use a hidden terminal prompt to install it without putting it in shell history.

```bash
(
  set -e
  umask 077
  cd /home/docker/netbox/config/dns-sync
  IFS= read -rsp 'Cloudflare API token: ' token
  printf '\n'
  test -n "$token"
  temporary=$(mktemp .cloudflare-token.XXXXXXXX)
  trap 'rm -f -- "$temporary"' EXIT
  printf '%s\n' "$token" > "$temporary"
  mv -f -- "$temporary" cloudflare.token
)
```

Run `run-dns-refresh.sh --preview` from the sync directory after saving or replacing the token. The `cloudflare_api` summary shows how many zones returned complete lists and any access errors. The sync imports new records and changes to proxy origins for active Cloudflare zones whose API nameservers match public delegation. Known API records remain protected if a token or endpoint fails. Local and Name.com lookups continue, and conflicting local or linked records stay in place for review. Cloudflare serves its own public addresses for proxied records. NetBox records the configured origin and represents Cloudflare's automatic TTL as 300 seconds. Flattened apex CNAMEs are recorded as inactive aliases to fit NetBox's DNS model.

## Name.com API access

The sync calls [Name.com's production DNS list endpoint](https://docs.name.com/api/v1/reference/dns/list-records) using a Name.com API username and token. It fetches only `rantrs.net` while that zone is active in NetBox and publicly delegated to Name.com. Use a dedicated production credential with DNS read access if offered by [Name.com API settings](https://www.name.com/account/settings/api).

Install [the credential helper](scripts/dns-sync/set-namecom-credentials.sh) on the Docker host and use it to store the username and token through hidden prompts.

```bash
target=/home/docker/netbox/config/dns-sync
install -m 700 scripts/dns-sync/set-namecom-credentials.sh "$target/set-namecom-credentials.sh"
"$target/set-namecom-credentials.sh"
"$target/run-dns-refresh.sh" --preview
```

The helper writes `namecom.credentials` under the persistent sync directory with mode `0600`. NetBox reads it from `/config/dns-sync`. The `namecom_api` preview summary reports access status. An incomplete record list or credential error leaves API-managed records in place. Cloudflare sync and the public DNS checks continue. Provider-specific types that cannot be represented safely are counted and skipped for review.

## Local DNS from pfSense

Use a dedicated API key with the [pfSense REST API privilege](https://pfrest.org/AUTHENTICATION_AND_AUTHORIZATION/) for `GET /api/v2/services/dns_resolver/host_overrides`. The NetBox container uses verified HTTPS to read the complete Unbound Host Overrides list from the lab firewall.

Keep the service account enabled so pfREST can authorize its key. Assign only the DNS read privilege. Keep administrative, webConfigurator, and SSH privileges off the account. Store a unique random password in a password manager. Docker Lab authenticates with the API key.

```bash
target=/home/docker/netbox/config/dns-sync
install -m 700 scripts/dns-sync/set-pfsense-token.sh "$target/set-pfsense-token.sh"
"$target/set-pfsense-token.sh"
"$target/run-dns-refresh.sh" --preview
```

The helper saves the key in `pfsense.token` with mode `0600`. The `pfsense_api` summary reports the source status, configured overrides, and unique names. Eligible Host Overrides and their aliases become A and AAAA records in the **pfSense local DNS** view under the closest active managed domain. These NetBox zones document the resolver's local answers. Their SOA metadata is generated by NetBox. Include this view and `_default_` in the dashboard's **DNS hosts** filters to see both sets of answers.

The sync replaces changed addresses on records it manages and gives missing records a second successful observation before marking them inactive. A credential or API error leaves local records in place and lets the public providers continue. Dynamic DHCP registrations and records defined outside Host Overrides, including Unbound custom options, require their own source.

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
