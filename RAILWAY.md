# Railway Deployment Guide

This guide deploys the vLLM Semantic Router on Railway's single-container platform.

## Problem

The standard `vllm-sr serve` command uses Docker-in-Docker to spawn multiple containers
(router, envoy, dashboard, redis, postgres, milvus, jaeger, prometheus, grafana).
Railway doesn't provide Docker socket access, so the standard flow fails silently.

## Solution

This deployment uses `railway.Dockerfile` which builds a complete standalone image with:
- Go router binary (`/usr/local/bin/router`)
- Envoy proxy
- Redis server
- Python vllm-sr CLI

All services run **in-process** via `railway-start.sh` — no Docker-in-Docker needed.

## Setup Steps

### 1. Update `railway.toml`

Change the `dockerfilePath` to use the new Dockerfile:

```toml
[build]
builder = "DOCKERFILE"
dockerfilePath = "./railway.Dockerfile"
target = "final"

[deploy]
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10

[deploy.variables]
PORT = "8899"
VLLM_SR_SETUP_MODE = "false"

[deploy.start]
command = "/app/railway-start.sh"
```

### 2. Manual Changes Required

Since existing files cannot be edited, manually update your `railway.toml`:

```bash
# Show current railway.toml
cat railway.toml

# Replace the content with the above config
```

### 3. Deploy

Push to your Railway project. The build will:
1. Build Rust candle/ML/NLP bindings
2. Build Go router binary
3. Extract Envoy binary
4. Install Redis
5. Start all services in-process

### 4. Verify

After deployment, check the runtime logs. You should see:
```
[HH:MM:SS] === All services running ===
[HH:MM:SS]   Envoy HTTP:  http://0.0.0.0:8899
[HH:MM:SS]   Router API:  http://127.0.0.1:8080
[HH:MM:SS]   Redis:       127.0.0.1:6379
```

## Files

| File | Purpose |
|------|---------|
| `railway.Dockerfile` | Multi-stage build (Go router + Envoy + Redis) |
| `railway-start.sh` | In-process service orchestrator |
| `RAILWAY.md` | This guide |

## Architecture

```
Railway Container (single)
├── redis-server     → 127.0.0.1:6379 (internal cache)
├── envoy           → 0.0.0.0:8899 (HTTP front door)
└── router (Go)     → 127.0.0.1:8080 (gRPC/API)
```

## Ports

| Port | Service |
|------|---------|
| 8899 | Envoy HTTP (public) |
| 8080 | Router API (internal) |
| 6379 | Redis cache (internal) |
| 8700 | Dashboard (optional, if added) |

## Troubleshooting

### 502 Bad Gateway
- Check runtime logs for startup errors
- Verify `railway-start.sh` is executable
- Ensure PORT env var matches listener config

### Build fails
- Railway free tier has memory limits — ensure sufficient RAM
- The Go build stage needs ~2GB RAM
