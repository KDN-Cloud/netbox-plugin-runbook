#!/usr/bin/env bash
set -euo pipefail
umask 077
export PATH=/usr/local/bin:/usr/bin:/bin

base=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mode=${1:---preview}
apply=False
case "$mode" in
    --preview) ;;
    --apply) apply=True ;;
    *) printf 'Usage: %s [--preview|--apply]\n' "$0" >&2; exit 2 ;;
esac

exec 9>"$base/refresh.lock"
if ! flock -n 9; then
    printf 'DNS refresh already running\n'
    exit 0
fi

if ! hostnames=$(timeout 15 docker exec -i npm-app sh -s <<'SH'
for dir in /data/nginx/proxy_host /data/nginx/redirection_host; do
    for file in "$dir"/*.conf; do
        [ -f "$file" ] || continue
        awk '$1=="server_name" {for (i=2;i<=NF;i++) {sub(/;$/, "", $i); print tolower($i)}}' "$file"
    done
done
SH
); then
    printf 'Nginx Proxy Manager unavailable. Refreshing previously known names.\n' >&2
    hostnames=
fi

{
    printf 'CONFIGURED_HOSTS = '
    printf '%s\n' "$hostnames" |
        python3 -c 'import json, re, sys; print(json.dumps(sorted({name for name in sys.stdin.read().split() if re.fullmatch(r"[a-z0-9.-]+[.][a-z]+", name)})))'
    cat "$base/dns_refresh.py"
    printf '\nrefresh_dns(CONFIGURED_HOSTS, apply=%s)\n' "$apply"
} | timeout 240 docker exec -i NETBOX /lsiopy/bin/python /app/netbox/netbox/manage.py shell \
        -c 'import sys; exec(sys.stdin.read())'
