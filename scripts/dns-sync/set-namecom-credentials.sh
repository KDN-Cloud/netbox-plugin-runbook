#!/usr/bin/env bash
set -euo pipefail
umask 077
export LC_ALL=C
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! test -t 0; then
    printf 'Run this in an interactive SSH terminal.\n' >&2
    exit 1
fi
IFS= read -rsp 'Name.com production API username: ' username
printf '\n' >&2
IFS= read -rsp 'Name.com production API token: ' token
printf '\n' >&2
if [[ ! "$username" =~ ^[[:graph:]]{1,255}$ || "$username" == *:* || ! "$token" =~ ^[[:graph:]]{1,1024}$ ]]; then
    printf 'Enter a production username and one API token.\n' >&2
    exit 1
fi
temporary=$(mktemp .namecom-credentials.XXXXXXXX)
trap 'rm -f -- "$temporary"' EXIT
printf '%s\n%s\n' "$username" "$token" > "$temporary"
unset username token
mv -f -- "$temporary" namecom.credentials
printf 'Name.com credentials saved privately.\n'
