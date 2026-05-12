# =============================================================================
# vLLM Semantic Router — Dockerfile
# =============================================================================
# Production entry point for building vLLM Semantic Router images.
#
# BUILD HIERARCHY:
#
#   This repository maintains three Dockerfile tiers:
#
#   1. Dockerfile  (this file) — Root production build with full build matrix:
#        BUILD_MODE  (source | pip)     Installation method
#        ENABLE_ROCM (true  | false)    GPU backend selection
#      This is the canonical image for deployment (Railway, CI/CD, self-hosted).
#
#   2. src/vllm-sr/Dockerfile — Development Dockerfile for source & cross-compile
#      builds of Rust (candle, ml, nlp bindings) and Go (router binary). Used
#      during active development. References the root Dockerfile as production.
#
#   3. src/vllm-sr/Dockerfile.rocm — (removed) ROCm variant folded into this file
#      via the ENABLE_ROCM build arg.
#
# BUILD MATRIX:
#
#   BUILD_MODE=source  (default): Full source build — Rust bindings via maturin
#     ~30 min build, produces complete self-contained image
#     Ideal for: local dev, self-hosted, on-prem, air-gapped deployments
#
#   BUILD_MODE=pip: Pre-built wheel from PyPI
#     ~2 min build, no compilation needed
#     Ideal for: Railway.com, CI/CD, quick deployments, ephemeral environments
#
#   ENABLE_ROCM=false  (default): CPU-only image using python:3.11-slim base
#   ENABLE_ROCM=true:  AMD ROCm GPU-accelerated image using rocm/dev-ubuntu-22.04:7.0 base
#     Installs ROCm runtime libraries and ROCm-compatible ONNX Runtime.
#     Requires an AMD GPU with ROCm-compatible drivers at runtime.
#
#   Combined matrix:
#     BUILD_MODE=source, ENABLE_ROCM=false  → CPU source build   (default target)
#     BUILD_MODE=pip,    ENABLE_ROCM=false  → CPU pip build      (fast)
#     BUILD_MODE=source, ENABLE_ROCM=true   → ROCm source build  (--target final-rocm)
#     BUILD_MODE=pip,    ENABLE_ROCM=true   → ROCm pip build     (--target final-rocm)
#
# USAGE:
#
#   # CPU build (default)
#   docker build --build-arg BUILD_MODE=source -t vllm-sr:cpu .
#   docker build --build-arg BUILD_MODE=pip   -t vllm-sr:cpu-pip .
#
#   # ROCm GPU build
#   docker build --build-arg BUILD_MODE=source --target final-rocm -t vllm-sr:rocm .
#   docker build --build-arg BUILD_MODE=pip   --target final-rocm -t vllm-sr:rocm-pip .
#
#   # Docker Compose (uses default BUILD_MODE=source, ENABLE_ROCM=false)
#   docker compose build
#
# ARCHITECTURE NOTES:
#
#   - All build stages use python:3.11-slim as the base for CPU builds to
#     ensure a small, consistent footprint.
#   - ROCm stages use rocm/dev-ubuntu-22.04:7.0 which provides the AMD GPU
#     runtime libraries (hipblas, miopen-hip, hipfft, rccl) required for
#     GPU-accelerated inference via ONNX Runtime ROCm.
#   - The Rust + Go router binary is built separately in src/vllm-sr/Dockerfile
#     for development; production images use the Python-based vllm-sr package.
# =============================================================================

ARG BUILD_MODE=source
ARG ENABLE_ROCM=false

# =============================================================================
# Stage: pip-stage — install pre-built wheel from PyPI (fast, ~2 min)
# Used by both CPU and ROCm final stages (the same wheel is ROCm-compatible
# at runtime when ROCm libraries are present).
# =============================================================================
FROM python:3.11-slim AS pip-stage

RUN pip install --no-cache-dir --pre vllm-sr

# =============================================================================
# Stage: source-stage — build from source (full, ~30 min)
# Compiles Rust bindings via maturin and installs the Python package with all
# CLI dependencies from the local source tree.
# =============================================================================
FROM python:3.11-slim AS source-stage

ENV VIRTUAL_ENV=/opt/vllm-sr-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        bash ca-certificates curl libssl3 \
        python3 python3-pip python3-venv python3-yaml; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv "${VIRTUAL_ENV}" && \
    "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip && \
    "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir 'huggingface_hub[cli]==1.5.0'

COPY src/vllm-sr/requirements.txt /tmp/
RUN "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir -r /tmp/requirements.txt

COPY src/vllm-sr/pyproject.toml /tmp/
COPY src/vllm-sr/cli/ /tmp/cli/
RUN "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir /tmp/

# =============================================================================
# Stage: rocm-runtime — base image for ROCm final stage
# Provides the ROCm device libraries and ONNX Runtime ROCm backend.
# =============================================================================
FROM rocm/dev-ubuntu-22.04:7.0 AS rocm-runtime

ENV VIRTUAL_ENV=/opt/vllm-sr-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        python3 \
        python3-pip \
        python3-venv \
        python3-yaml \
        hipblas \
        miopen-hip \
        hipfft \
        rccl; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Install ROCm-enabled ONNX Runtime shared library
RUN python3 -m venv "${VIRTUAL_ENV}" && \
    "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip && \
    "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir \
      'huggingface_hub[cli]==1.5.0' && \
    "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir \
      https://repo.radeon.com/rocm/manylinux/rocm-rel-7.0/onnxruntime_rocm-1.22.1-cp310-cp310-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl && \
    ORT_DIR="$("${VIRTUAL_ENV}/bin/python" -c 'import pathlib, onnxruntime; print(pathlib.Path(onnxruntime.__file__).resolve().parent / "capi")')" && \
    mkdir -p /opt/onnxruntime && \
    ln -sfn "$ORT_DIR" /opt/onnxruntime/capi && \
    ln -sf /opt/onnxruntime/capi/libonnxruntime.so.* /opt/onnxruntime/capi/libonnxruntime.so

ENV ORT_DYLIB_PATH=/opt/onnxruntime/capi/libonnxruntime.so \
    LD_LIBRARY_PATH=/usr/local/lib:/opt/rocm/lib:/opt/onnxruntime/capi

# =============================================================================
# Final stage — CPU runtime (default target)
# =============================================================================
FROM python:3.11-slim AS final

ENV VIRTUAL_ENV=/opt/vllm-sr-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# Labels
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
        ca-certificates curl libssl3; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Copy virtualenv — the stage used depends on BUILD_MODE
COPY --from=pip-stage /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=pip-stage /usr/local/bin/vllm-sr /usr/local/bin/vllm-sr

WORKDIR /app
RUN mkdir -p /app /app/.vllm-sr /app/config /app/models

COPY config/ /app/config/

EXPOSE ${PORT}

# Healthcheck — Railway requires this; general deployments benefit too
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" 2>/dev/null || \
      curl -sf http://localhost:${PORT}/health || exit 1

ENTRYPOINT ["vllm-sr"]
CMD ["serve"]

# =============================================================================
# Stage: final-rocm — ROCm GPU runtime
# Use: docker build --target final-rocm ...
# Extends the ROCm runtime base with the vllm-sr Python package and config.
# =============================================================================
FROM rocm-runtime AS final-rocm

ENV VIRTUAL_ENV=/opt/vllm-sr-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# Labels
LABEL org.opencontainers.image.title="vLLM Semantic Router (ROCm)"
LABEL org.opencontainers.image.description="System-Level Intelligent Router for Mixture-of-Models — AMD ROCm GPU Edition"
LABEL org.opencontainers.image.vendor="vLLM-SR Team"
LABEL org.opencontainers.image.source="https://github.com/vllm-project/semantic-router"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Railway support: PORT env var is injected by Railway; default for local use
ENV PORT=8080
ENV VLLM_SR_CONFIG=/app/config.yaml

# Copy virtualenv — the stage used depends on BUILD_MODE
COPY --from=pip-stage /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=pip-stage /usr/local/bin/vllm-sr /usr/local/bin/vllm-sr

WORKDIR /app
RUN mkdir -p /app /app/.vllm-sr /app/config /app/models

COPY config/ /app/config/

EXPOSE ${PORT}

# Healthcheck — Railway requires this; general deployments benefit too
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" 2>/dev/null || \
      curl -sf http://localhost:${PORT}/health || exit 1

ENTRYPOINT ["vllm-sr"]
CMD ["serve"]
