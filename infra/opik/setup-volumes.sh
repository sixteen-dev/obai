#!/usr/bin/env bash
# Create external Docker volumes for Opik (one-time setup).
# These volumes survive `docker compose down -v`.

set -euo pipefail

volumes=(
  opik-mysql-data
  opik-redis-data
  opik-zookeeper-data
  opik-clickhouse-data
  opik-clickhouse-logs
  opik-clickhouse-config
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

echo "Done. Run: docker compose up -d"
