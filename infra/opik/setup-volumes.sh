#!/usr/bin/env bash
# Create external Docker volumes for Opik (one-time setup).
# These volumes survive `docker compose down -v`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLICKHOUSE_CONFIG_DIR="$SCRIPT_DIR/clickhouse_config"
NGINX_CONF_FILE="$SCRIPT_DIR/nginx_default_local.conf"

volumes=(
  opik-mysql-data
  opik-redis-data
  opik-zookeeper-data
  opik-clickhouse-data
  opik-clickhouse-logs
  opik-clickhouse-config
  opik-frontend-config
  opik-minio-data
)

for vol in "${volumes[@]}"; do
  if docker volume inspect "$vol" &>/dev/null; then
    echo "  exists: $vol"
  else
    docker volume create "$vol"
    echo "  created: $vol"
  fi
done

if [ ! -d "$CLICKHOUSE_CONFIG_DIR" ]; then
  echo "Missing ClickHouse config directory: $CLICKHOUSE_CONFIG_DIR" >&2
  exit 1
fi

if ! find "$CLICKHOUSE_CONFIG_DIR" -mindepth 1 -print -quit | grep -q .; then
  echo "ClickHouse config directory is empty: $CLICKHOUSE_CONFIG_DIR" >&2
  exit 1
fi

tar -C "$CLICKHOUSE_CONFIG_DIR" -cf - . \
  | docker run --rm -i -v opik-clickhouse-config:/config alpine:latest sh -c '
      rm -rf /config/*
      tar -xf - -C /config
      chown -R 1000:1000 /config
    '
echo "  synced: opik-clickhouse-config"

if [ ! -f "$NGINX_CONF_FILE" ]; then
  echo "Missing nginx config file: $NGINX_CONF_FILE" >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
cp "$NGINX_CONF_FILE" "$tmp_dir/default.conf"

tar -C "$tmp_dir" -cf - . \
  | docker run --rm -i -v opik-frontend-config:/config alpine:latest sh -c '
      rm -rf /config/*
      tar -xf - -C /config
      chown -R 101:101 /config
      find /config -type d -exec chmod 755 {} +
      find /config -type f -exec chmod 664 {} +
    '
echo "  synced: opik-frontend-config"

echo "Done. Run: docker compose up -d"
