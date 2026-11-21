#!/usr/bin/env bash
set -euo pipefail
umask 077
export LC_ALL=C
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! test -t 0; then
    printf 'Run this in an interactive SSH terminal.\n' >&2
    exit 1
fi
IFS= read -rsp 'pfSense read-only DNS API key: ' token
printf '\n' >&2
if [[ ! "$token" =~ ^[[:graph:]]{1,1024}$ ]]; then
    printf 'Enter one API key.\n' >&2
    exit 1
fi
temporary=$(mktemp .pfsense-token.XXXXXXXX)
trap 'rm -f -- "$temporary"' EXIT
printf '%s\n' "$token" > "$temporary"
unset token
mv -f -- "$temporary" pfsense.token
printf 'pfSense DNS API key saved privately.\n'
