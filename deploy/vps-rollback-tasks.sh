#!/bin/bash
# Rollback canary tasks : stoppe le canary, verifie la prod intacte.
# Aucune modification du service prod (jamais pointe vers le binaire Rust).
set -euo pipefail
sudo systemctl stop tasks-gateway-rs.service || true
sudo systemctl is-active tasks-gateway.service
curl -s http://127.0.0.1:8792/health; echo
echo "[rollback] prod :8792 intacte, canary stoppe"
