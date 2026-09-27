#!/usr/bin/env bash
# Create the named Cloudflare tunnel for Email Blaster, point the subdomain at
# it, and write the config the cloudflared container mounts.  Run once:
#
#   cloudflared tunnel login        # you, in a browser — authorises the zone
#   ./scripts/named-tunnel-setup.sh
#
# Safe to re-run: reuses an existing tunnel of the same name and overwrites
# the DNS record rather than erroring on the second pass.
#
# ONLY the paths an outside caller needs are exposed — the MCP endpoint Muse
# connects to, provider webhooks, and the unsubscribe links in sent email.
# The dashboard and the rest of the API stay on localhost.
set -euo pipefail
cd "$(dirname "$0")/.."

TUNNEL_NAME="${TUNNEL_NAME:-outreach}"
TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME:-outreach.blueprintautomation.tech}"
TUNNEL_SERVICE="${TUNNEL_SERVICE:-http://backend:8000}"
CERT="${HOME}/.cloudflared/cert.pem"
OUT="secrets/cloudflared"

command -v cloudflared >/dev/null || { echo "cloudflared is not installed (brew install cloudflared)" >&2; exit 1; }
if [ ! -f "$CERT" ]; then
  echo "No Cloudflare certificate at $CERT — run 'cloudflared tunnel login' first and pick the zone." >&2
  exit 1
fi

tunnel_id() {
  cloudflared tunnel list --output json | python3 -c "
import sys, json
name = sys.argv[1]
print(next((t['id'] for t in json.load(sys.stdin) if t['name'] == name), ''))
" "$TUNNEL_NAME"
}

# Reuse rather than recreate: the DNS record points at the UUID.
uuid=$(tunnel_id)
if [ -n "$uuid" ]; then
  echo "==> reusing existing tunnel '$TUNNEL_NAME' ($uuid)"
else
  echo "==> creating tunnel '$TUNNEL_NAME'"
  cloudflared tunnel create "$TUNNEL_NAME" >/dev/null
  uuid=$(tunnel_id)
  [ -n "$uuid" ] || { echo "tunnel was created but did not appear in the list" >&2; exit 1; }
  echo "    $uuid"
fi

creds="${HOME}/.cloudflared/${uuid}.json"
[ -f "$creds" ] || { echo "no credentials file at $creds" >&2; exit 1; }

echo "==> pointing $TUNNEL_HOSTNAME at the tunnel"
cloudflared tunnel --overwrite-dns route dns "$TUNNEL_NAME" "$TUNNEL_HOSTNAME"

mkdir -p "$OUT"
cp "$creds" "$OUT/credentials.json"
chmod 600 "$OUT/credentials.json"

# Rules are matched top to bottom; the last must be a catch-all.  Anything
# not on the allowlist gets a 404 at Cloudflare's edge and never reaches us.
cat > "$OUT/config.yml" <<CFG
tunnel: ${uuid}
credentials-file: /etc/cloudflared/credentials.json
metrics: 0.0.0.0:2000

ingress:
  - hostname: ${TUNNEL_HOSTNAME}
    path: ^/(mcp$|webhooks/|unsubscribe/|health$)
    service: ${TUNNEL_SERVICE}
  - hostname: ${TUNNEL_HOSTNAME}
    service: http_status:404
  - service: http_status:404
CFG

echo "==> validating the ingress rules"
cloudflared tunnel --config "$OUT/config.yml" ingress validate

cat <<MSG

Tunnel ready.

  name      $TUNNEL_NAME
  uuid      $uuid
  hostname  https://${TUNNEL_HOSTNAME}
  config    $OUT/config.yml (credentials.json alongside it — gitignored)

Set in .env:

  PUBLIC_ORIGIN=https://${TUNNEL_HOSTNAME}
  WEBHOOK_BASE_URL=https://${TUNNEL_HOSTNAME}

Then bring the stack up on it:

  docker compose -f docker-compose.yml -f docker-compose.named-tunnel.yml up -d --force-recreate backend worker beat cloudflared

MSG
