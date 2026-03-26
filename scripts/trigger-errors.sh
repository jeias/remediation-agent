#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="us-east-2"
AWS_PROFILE="dev"
ALB_DNS=$(cd "$(dirname "$0")/../infra" && terraform output -raw alb_dns_name)
COUNT="${1:-15}"

echo "ALB: $ALB_DNS"
echo ""

echo "=== Health Check ==="
curl -s "http://$ALB_DNS/health" | python3 -m json.tool
echo ""

echo "=== Items ==="
curl -s -w "\nHTTP Status: %{http_code}\n" "http://$ALB_DNS/items" | head -20
echo ""

echo "=== Sending $COUNT requests to /items ==="
for i in $(seq 1 "$COUNT"); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://$ALB_DNS/items")
  echo "  $i/$COUNT — $CODE"
  sleep 1
done

echo ""
echo "=== Checking alarm state ==="
for i in $(seq 1 9); do
  sleep 10
  STATE=$(aws cloudwatch describe-alarms --alarm-names "remediation-agent-error-rate" --region "$AWS_REGION" --profile "$AWS_PROFILE" --query 'MetricAlarms[0].StateValue' --output text)
  echo "  ${i}0s — Alarm: $STATE"
  if [ "$STATE" = "ALARM" ]; then
    echo ""
    echo "ALARM triggered! Watch agent logs:"
    echo "  aws logs tail /aws/lambda/remediation-agent-lambda --follow --region $AWS_REGION --profile $AWS_PROFILE"
    exit 0
  fi
done

echo ""
echo "Alarm did not trigger. Current state: $STATE"
