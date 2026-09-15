#!/bin/bash
# Verification canary tasks : sante + fail-closed, prod intacte. Lecture seule.
set -u
export HOME=/home/juliann
echo "=== canary :18992 ==="
curl -s --max-time 5 http://127.0.0.1:18992/health; echo
curl -s --max-time 5 http://127.0.0.1:18992/ready; echo
curl -s -o /dev/null -w "POST /mcp sans auth -> %{http_code}\n" --max-time 5 \
  -X POST http://127.0.0.1:18992/mcp -H "content-type: application/json" -d '{}' || true
systemctl is-active tasks-gateway-rs.service
echo "=== prod :8792 intacte ==="
curl -s --max-time 5 http://127.0.0.1:8792/health; echo
systemctl is-active tasks-gateway.service tasks-mcp.service
