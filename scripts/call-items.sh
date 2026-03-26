#!/usr/bin/env bash
set -euo pipefail

ALB_DNS=$(cd "$(dirname "$0")/../infra" && terraform output -raw alb_dns_name)

echo "Calling GET /items on $ALB_DNS (Ctrl+C to stop)"
echo ""

while true; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://$ALB_DNS/items")
  echo "$(date +%H:%M:%S) — $CODE"
  sleep 1
done
