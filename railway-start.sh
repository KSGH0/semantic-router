#!/bin/bash
# =============================================================================
# vLLM Semantic Router — Railway Standalone Startup
# =============================================================================
# No Docker-in-Docker. Runs router + envoy + redis in a single container.
# For Railway's single-container (no Docker socket) deployment.
# =============================================================================

set -euo pipefail

CONFIG_FILE="${VLLM_SR_CONFIG:-/app/config.yaml}"
ENVOY_CONFIG_FILE="/app/.vllm-sr/envoy.yaml"
ROUTER_PID=""
ENVOY_PID=""
CACHE_PID=""

log()  { printf "[%s] %s\n" "$(date '+%H:%M:%S')" "$*"; }
warn() { log "WARNING: $*"; }

cleanup() {
    log "Shutting down..."
    for pid in "${ENVOY_PID:-}" "${ROUTER_PID:-}" "${CACHE_PID:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null || true
    done
    log "Shutdown complete."
}
trap cleanup SIGTERM SIGINT EXIT

# 1. Start Redis
if command -v redis-server &>/dev/null; then
    log "Starting redis-server on 127.0.0.1:6379..."
    redis-server --daemonize yes --bind 127.0.0.1 --port 6379 --save "" --appendonly no
    sleep 2
    CACHE_PID=$(pgrep -x redis-server 2>/dev/null | head -1) || ""
fi

# 2. Ensure config.yaml exists
if [ ! -f "${CONFIG_FILE}" ]; then
    log "Creating default config.yaml..."
    cat > "${CONFIG_FILE}" <<EOF
version: v0.3
listeners:
  - name: http-8899
    address: 0.0.0.0
    port: 8899
    timeout: 300s
setup:
  mode: false
  state: bootstrap
EOF
fi

# 3. Generate Envoy config
mkdir -p /app/.vllm-sr
if python3 -m cli.config_generator "${CONFIG_FILE}" "${ENVOY_CONFIG_FILE}" 2>/dev/null; then
    log "Envoy config generated"
else
    warn "config_generator failed — using inline config"
    cat > "${ENVOY_CONFIG_FILE}" <<EOF
static_resources:
  listeners:
  - name: http_ingress
    address:
      socket_address: { address: 0.0.0.0, port_value: 8899 }
    filter_chains:
    - filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: ingress_http
          codec_type: AUTO
          route_config:
            name: local_route
            virtual_hosts:
            - name: backend
              domains: ["*"]
              routes:
              - match: { prefix: "/" }
                route: { cluster: router_cluster }
          http_filters:
          - name: envoy.filters.http.router
  clusters:
  - name: router_cluster
    connect_timeout: 5s
    type: STRICT_DNS
    lb_policy: ROUND_ROBIN
    load_assignment:
      cluster_name: router_cluster
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address: { address: 127.0.0.1, port_value: 8080 }
admin:
  address:
    socket_address: { address: 127.0.0.1, port_value: 9901 }
EOF
fi

# 4. Start Envoy
if command -v envoy &>/dev/null; then
    log "Starting Envoy on 0.0.0.0:8899..."
    envoy -c "${ENVOY_CONFIG_FILE}" --log-level info &
    ENVOY_PID=$!
fi

# 5. Start Router
if [ -x /usr/local/bin/router ]; then
    log "Starting router (Go binary) on :8080..."
    /usr/local/bin/router \
        -config="${CONFIG_FILE}" \
        -port=50051 \
        -enable-api=true \
        -api-port=8080 &
    ROUTER_PID=$!
fi

log "=== All services running ==="
log "  Envoy HTTP:  http://0.0.0.0:8899"
log "  Router API:  http://127.0.0.1:8080"
log "  Redis:       127.0.0.1:6379"
log ""

# Monitor
while true; do
    sleep 10
    for name in ENVOY ROUTER; do
        pid_var="${name}_PID"
        pid="${!pid_var}"
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            warn "${name} exited"
            eval "${pid_var}="
        fi
    done
done
