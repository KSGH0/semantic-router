# =============================================================================
# vLLM Semantic Router — Unified Dockerfile (CPU + AMD ROCm)
# =============================================================================
# Single Dockerfile that adapts to both CPU-only (Railway) and AMD GPU
# (ROCm) deployments via build args and multi-stage targets.
#
# TARGETS:
#   final        (default) — CPU-only runtime   (~180 MB)
#   final-rocm             — ROCm/GPU runtime   (~3.8 GB, x86_64 only)
#
# BUILD ARGS:
#   ENABLE_ROCM  (default: "false")
#     When "true", sets environment variables for ROCm/GPU support in the
#     final-rocm target. The final (CPU) target ignores this arg.
#
# USAGE:
#   # CPU build (default, Railway-compatible)
#   docker build -t vllm-sr:cpu .
#
#   # ROCm/GPU build (x86_64 only)
#   docker build \
#     --build-arg ENABLE_ROCM=true \
#     --target final-rocm \
#     -t vllm-sr:rocm .
#
#   # Force CPU target explicitly
#   docker build --target final -t vllm-sr:cpu .
#
# ARCHITECTURE NOTES:
#   - ROCm/GPU target is x86_64 only (ROCm does not support arm64 for this
#     image).
#   - CPU target supports both x86_64 and arm64.
#   - ROCm runtime image is based on rocm/dev-ubuntu-22.04:7.0 with Python
#     3.10; the vllm-sr package is pure Python and installs cleanly on both
#     Python 3.10 and 3.11.
#
# RAILWAY USAGE (railway.toml):
#   [build]
#   builder = "DOCKERFILE"
#   dockerfilePath = "./Dockerfile"
#   target = "final"
#
# COMPANION FILES (removed in unification):
#   Dockerfile.full was merged into this single Dockerfile.
# =============================================================================

ARG ENABLE_ROCM=false

# =============================================================================
# Stage: build — install vllm-sr Python package from PyPI (Python 3.11)
# Shared build stage used by both final (CPU) and final-rocm (GPU) targets.
# The package is pure Python so it works on both Python 3.11 and 3.10.
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
# Stage: rocm-runtime — ROCm 7.0 runtime image with GPU libraries
# Used exclusively by the final-rocm target. Provides:
#   - ROCm 7.0 base (rocm/dev-ubuntu-22.04:7.0)
#   - ROCm runtime libraries (hipBLAS, MIOpen, hipFFT, RCCL)
#   - ROCm-enabled ONNX Runtime (onnxruntime-rocm)
#   - Python 3.10 virtual environment
# =============================================================================
FROM rocm/dev-ubuntu-22.04:7.0 AS rocm-runtime

# Install ROCm runtime libraries and Python tooling
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        python3-yaml \
        ca-certificates \
        curl \
        hipblas \
        miopen-hip \
        hipfft \
        rccl; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Create virtual environment and install ROCm-enabled ONNX Runtime
# This wheel is the official AMD build for ROCm 7.0 (Python 3.10).
RUN python3 -m venv /opt/vllm-sr-venv && \
    /opt/vllm-sr-venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/vllm-sr-venv/bin/pip install --no-cache-dir \
        https://repo.radeon.com/rocm/manylinux/rocm-rel-7.0/onnxruntime_rocm-1.22.1-cp310-cp310-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl

ENV VIRTUAL_ENV=/opt/vllm-sr-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# =============================================================================
# Stage: final — CPU-only runtime (default target)
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

# Runtime configuration
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

# =============================================================================
# Stage: final-rocm — ROCm/GPU runtime (used when --target final-rocm)
# Based on rocm/dev-ubuntu-22.04:7.0 with full AMD GPU acceleration.
# x86_64 only — ROCm does not support arm64 for this image.
#
# Run with:
#   docker run --device=/dev/kfd --device=/dev/dri --group-add video \
#     -v /path/to/config.yaml:/app/config.yaml \
#     vllm-sr:rocm
# =============================================================================
FROM rocm-runtime AS final-rocm

ARG ENABLE_ROCM
ENV ENABLE_ROCM=${ENABLE_ROCM}
ENV VIRTUAL_ENV=/opt/vllm-sr-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# OCI labels
LABEL org.opencontainers.image.title="vLLM Semantic Router (ROCm)"
LABEL org.opencontainers.image.description="System-Level Intelligent Router for Mixture-of-Models — AMD GPU (ROCm) edition"
LABEL org.opencontainers.image.vendor="vLLM-SR Team"
LABEL org.opencontainers.image.source="https://github.com/vllm-project/semantic-router"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Runtime configuration
ENV PORT=8080
ENV VLLM_SR_CONFIG=/app/config.yaml

# Install vllm-sr from PyPI into the Python 3.10 venv
RUN /opt/vllm-sr-venv/bin/pip install --no-cache-dir --pre vllm-sr

WORKDIR /app
RUN mkdir -p /app/.vllm-sr /app/config /app/models

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" 2>/dev/null || \
      curl -sf http://localhost:${PORT}/health || exit 1

EXPOSE ${PORT}

ENTRYPOINT ["vllm-sr"]
CMD ["serve"]
