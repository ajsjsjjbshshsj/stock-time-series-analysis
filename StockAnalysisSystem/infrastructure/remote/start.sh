#!/bin/sh
set -eu
unsafe() { echo 'Unsafe gateway configuration' >&2; exit 1; }
case "${SERVER_NAME:-}" in ''|*[!A-Za-z0-9.-]*) unsafe ;; esac
[ "${AUTH_ORIGIN:-}" = "https://${SERVER_NAME}" ] || unsafe
for upstream in "${AUTH_UPSTREAM:-}" "${MARKET_UPSTREAM:-}" "${CONSUMER_UPSTREAM:-}"; do
  case "$upstream" in http://127.0.0.1:*) port=${upstream#http://127.0.0.1:} ;; *) unsafe ;; esac
  case "$port" in ''|*[!0-9]*) unsafe ;; esac
  [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || unsafe
done
[ -r /run/tls/server.crt ] && [ -r /run/tls/server.key ] || { echo 'TLS files required' >&2; exit 1; }
# Refuse to proxy a local/anonymous backend or an origin with mismatched cookies.
headers=$(wget -S -O /dev/null "${AUTH_UPSTREAM}/api/auth/readiness" 2>&1) || { echo 'Protected auth backend is not ready' >&2; exit 1; }
mode=$(printf '%s\n' "$headers" | awk 'tolower($1)=="x-auth-mode:" {gsub("\r", "", $2); print $2}')
origin=$(printf '%s\n' "$headers" | awk 'tolower($1)=="x-auth-origin:" {gsub("\r", "", $2); print $2}')
[ "$mode" = protected ] && [ "$origin" = "$AUTH_ORIGIN" ] || { echo 'Protected auth profile/origin mismatch' >&2; exit 1; }
envsubst '${SERVER_NAME} ${AUTH_UPSTREAM} ${MARKET_UPSTREAM} ${CONSUMER_UPSTREAM}' < /gateway/nginx.conf.template > /etc/nginx/conf.d/default.conf
nginx -t
exec nginx -g 'daemon off;'
