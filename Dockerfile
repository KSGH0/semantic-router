# =============================================================================
# vLLM Semantic Router — Dockerfile (CPU-only, Railway-optimized)
# =============================================================================
# Lightweight production image optimized for Railway's CPU-only environment.
# Installs vllm-sr from PyPI — no compilation, no GPU/ROCm support.
#
# Final image size: ~180 MB
# Build time: ~2 minutes
#
# WHY THIS FILE EXISTS:
#   This is the default Dockerfile for Railway deployments. It produces the
#   smallest possible image by stripping all GPU/ROCm dependencies and using
#   a pre-built wheel from PyPI. No Rust/Go compilation is performed.
#
#   For self-hosted deployments requiring AMD GPU (ROCm) acceleration, use
#   the companion Dockerfile.full instead.
#
# USAGE:
#   docker build -t vllm-sr:cpu .
#
# RAILWAY USAGE (railway.toml):
#   [build]
#   builder = "DOCKERFILE"
#   dockerfilePath = "./Dockerfile"
#   target = "final"
#
# COMPANION FILE:
#   Dockerfile.full  — Full build matrix with ROCm/GPU support for self-hosted
# =============================================================================

# =============================================================================
# Stage: build — install vllm-sr Python package from PyPI
# =============================================================================
FROM python:3.11-slim AS build

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Install vllm-sr pre-release from PyPI (pure Python, no compilation)
RUN pip install --no-cache-dir --pre vllm-sr

# =============================================================================
# Stage: final — CPU runtime (single target)
# =============================================================================
FROM python:3.11-slim AS final

ENV VIRTUAL_ENV=/opt/vllm-sr-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# OCI labels
LABEL org.opencontainers.image.title="vLLM Semantic Router"
LABEL org.opencontainers.image.description="System-Level Intelligent Router for Mixture-of-Models"
LABEL org.opencontainers.image.vendor="vLLM-SR Team"
LABEL org.opencontainers.image.source="https://github.com/vllm-project/semantic-router"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Railway support: PORT env var is injected by Railway; default for local use
ENV PORT=8080
ENV VLLM_SR_CONFIG=/app/config.yaml

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Copy installed package from build stage
COPY --from=build /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=build /usr/local/bin/vllm-sr /usr/local/bin/vllm-sr

WORKDIR /app
RUN mkdir -p /app/.vllm-sr /app/config /app/models

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" 2>/dev/null || \
      curl -sf http://localhost:${PORT}/health || exit 1

EXPOSE ${PORT}

ENTRYPOINT ["vllm-sr"]
CMD ["serve"]
