#!/bin/bash
# Starts all vLLM Semantic Router services in-process (no Docker)
# Used for Modal.com deployment.

set -e

CONFIG_FILE="${VLLM_SR_CONFIG:-/app/config/config.yaml}"
ENVOY_CONFIG_FILE="/etc/envoy/envoy.yaml"

echo "=== vLLM Semantic Router — Modal startup ==="
echo "  Config : $CONFIG_FILE"

# 1. Redis
echo "[1/3] Starting Redis..."
redis-server --daemonize yes --bind 127.0.0.1 --port 6379

# 2. Go router (background)
echo "[2/3] Starting router (gRPC :50051, API :8080)..."
/usr/local/bin/router \
    -config="$CONFIG_FILE" \
    -port=50051 \
    -enable-api=true \
    -api-port=8080 &

# Wait for router API to be healthy (up to 60 s)
echo "Waiting for router health check..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
        echo "Router ready."
        break
    fi
    sleep 2
done

# 3. Envoy — use pre-built static config (baked into image at build time)
echo "[3/3] Starting Envoy on port ${PORT:-8899}..."
exec /usr/local/bin/envoy \
    -c "$ENVOY_CONFIG_FILE" \
    --log-level warn \
    --log-format '[%Y-%m-%d %T.%e][%l] %v'
