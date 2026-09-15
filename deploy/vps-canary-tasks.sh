#!/bin/bash
# Canary VPS tasks-gateway-rs : toolchain + build release + install + demarrage.
# Idempotent. Aucune modification du service prod tasks-gateway.service.
set -euo pipefail
export HOME=/home/juliann
export PATH="$HOME/.cargo/bin:$PATH"
BUILD=/home/juliann/build/mcp-rust-migration

cargo --version
rustup component add rustfmt clippy 2>/dev/null || true
cd "$BUILD/tasks-gateway-rs"
echo "[canary] fmt/clippy/test…"
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
echo "[canary] build release…"
cargo build --release
echo "[canary] installation /opt/tasks-gateway-rs…"
sudo install -d -o tasks-app -g tasks-app -m 0755 /opt/tasks-gateway-rs
sudo install -m 0755 target/release/tasks-gateway-rs /opt/tasks-gateway-rs/tasks-gateway-rs
sudo install -m 0644 deploy/tasks-gateway-rs.service /etc/systemd/system/tasks-gateway-rs.service
sudo systemctl daemon-reload
echo "[canary] demarrage tasks-gateway-rs.service (:18992)…"
sudo systemctl enable --now tasks-gateway-rs.service
sleep 3
sudo systemctl is-active tasks-gateway-rs.service
curl -s http://127.0.0.1:18992/health; echo
curl -s http://127.0.0.1:18992/ready; echo
echo "[canary] OK"
