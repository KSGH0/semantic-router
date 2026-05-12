# =============================================================================
# vLLM Semantic Router — Railway Standalone Dockerfile
# =============================================================================
# Dedicated build for Railway's single-container constraint.
# Builds the Go router + Rust bindings, then adds Envoy + Redis.
# Runs everything in-process without Docker-in-Docker.
# =============================================================================

# ---- Stage: router-runtime (from src/vllm-sr/Dockerfile go-builder) ----
# We inline the Go build stages because Railway can't COPY from sibling Dockerfiles.
# This mirrors src/vllm-sr/Dockerfile stages but in a single file.

# Stage: build-rust-candle (mirrors src/vllm-sr/Dockerfile rust-builder)
FROM --platform=$BUILDPLATFORM rustlang/rust:nightly-bullseye AS build-rust-candle
ARG TARGETARCH
WORKDIR /build
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      dpkg --add-architecture arm64 && \
      apt-get update && apt-get install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu libssl-dev:arm64 libssl-dev pkg-config && \
      rm -rf /var/lib/apt/lists/* && \
      rustup target add aarch64-unknown-linux-gnu; \
    fi
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
COPY candle-binding/Cargo.toml candle-binding/Cargo.lock ./
RUN mkdir -p src && echo "pub fn _dummy() {}" > src/lib.rs && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      OPENSSL_LIB_DIR=/usr/lib/aarch64-linux-gnu cargo build --release --no-default-features --target aarch64-unknown-linux-gnu; \
    else \
      cargo build --release --no-default-features; \
    fi && rm -rf src
COPY candle-binding/src/ ./src/
COPY candle-binding/go.mod candle-binding/semantic-router.go ./
RUN find target -name "libcandle_semantic_router.so" -delete 2>/dev/null || true && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      OPENSSL_LIB_DIR=/usr/lib/aarch64-linux-gnu cargo build --release --no-default-features --target aarch64-unknown-linux-gnu; \
    else \
      cargo build --release --no-default-features; \
    fi
RUN mkdir -p /build/out && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      cp target/aarch64-unknown-linux-gnu/release/libcandle_semantic_router.so /build/out/; \
    else \
      cp target/release/libcandle_semantic_router.so /build/out/; \
    fi

# Stage: build-ml (mirrors src/vllm-sr/Dockerfile ml-builder)
FROM --platform=$BUILDPLATFORM rustlang/rust:nightly-bullseye AS build-ml
ARG TARGETARCH
WORKDIR /build
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
COPY ml-binding/Cargo.toml ml-binding/Cargo.lock ./
RUN mkdir -p src && echo "pub fn _dummy() {}" > src/lib.rs && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      rustup target add aarch64-unknown-linux-gnu && \
      cargo build --release --no-default-features --target aarch64-unknown-linux-gnu; \
    else \
      cargo build --release --no-default-features; \
    fi && rm -rf src
COPY ml-binding/src/ ./src/
COPY ml-binding/go.mod ml-binding/ml_binding.go ./
RUN find target -name "libml_semantic_router.so" -delete 2>/dev/null || true && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      cargo build --release --no-default-features --target aarch64-unknown-linux-gnu; \
    else \
      cargo build --release --no-default-features; \
    fi
RUN mkdir -p /build/out && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      cp target/aarch64-unknown-linux-gnu/release/libml_semantic_router.so /build/out/; \
    else \
      cp target/release/libml_semantic_router.so /build/out/; \
    fi

# Stage: build-nlp (mirrors src/vllm-sr/Dockerfile nlp-builder)
FROM --platform=$BUILDPLATFORM rustlang/rust:nightly-bullseye AS build-nlp
ARG TARGETARCH
WORKDIR /build
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
COPY nlp-binding/Cargo.toml nlp-binding/Cargo.lock ./
RUN mkdir -p src && echo "pub fn _dummy() {}" > src/lib.rs && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      rustup target add aarch64-unknown-linux-gnu && \
      cargo build --release --no-default-features --target aarch64-unknown-linux-gnu; \
    else \
      cargo build --release --no-default-features; \
    fi && rm -rf src
COPY nlp-binding/src/ ./src/
COPY nlp-binding/go.mod nlp-binding/nlp_binding.go nlp-binding/nlp_binding_mock.go ./
RUN find target -name "libnlp_binding.so" -delete 2>/dev/null || true && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      cargo build --release --no-default-features --target aarch64-unknown-linux-gnu; \
    else \
      cargo build --release --no-default-features; \
    fi
RUN mkdir -p /build/out && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      cp target/aarch64-unknown-linux-gnu/release/libnlp_binding.so /build/out/; \
    else \
      cp target/release/libnlp_binding.so /build/out/; \
    fi

# Stage: build-go (mirrors src/vllm-sr/Dockerfile go-builder)
FROM --platform=$BUILDPLATFORM golang:1.24-bullseye AS build-go
ARG TARGETARCH
WORKDIR /build
ENV LD_LIBRARY_PATH=/usr/local/lib
COPY --from=build-rust-candle /build/out/ /usr/local/lib/
COPY --from=build-ml /build/out/ /usr/local/lib/
COPY --from=build-nlp /build/out/ /usr/local/lib/
COPY --from=build-rust-candle /build/Cargo.toml /build/semantic-router.go /build/../candle-binding/
COPY --from=build-ml /build/out/ /build/../ml-binding/target/release/
COPY ml-binding/go.mod ml-binding/ml_binding.go /build/../ml-binding/
COPY --from=build-nlp /build/out/ /build/../nlp-binding/target/release/
COPY nlp-binding/go.mod nlp-binding/nlp_binding.go nlp-binding/nlp_binding_mock.go nlp-binding/
COPY src/semantic-router/ .
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      dpkg --add-architecture arm64 && \
      apt-get update && apt-get install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu libssl-dev:arm64 && \
      rm -rf /var/lib/apt/lists/* && \
      CC=aarch64-linux-gnu-gcc CGO_ENABLED=1 GOOS=linux GOARCH=arm64 \
      CGO_LDFLAGS="-L/usr/lib/aarch64-linux-gnu -lssl -lcrypto" \
      go build -buildvcs=false -ldflags="-w -s" -o router ./cmd; \
    else \
      CGO_ENABLED=1 go build -buildvcs=false -ldflags="-w -s" -o router ./cmd; \
    fi

# ---- Stage: envoy (pre-built image, just extract binary) ----
FROM envoyproxy/envoy:v1.34-latest AS envoy

# ---- Stage: final ----
FROM debian:bookworm-slim

ENV VIRTUAL_ENV=/opt/vllm-sr-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV LD_LIBRARY_PATH=/usr/local/lib
ENV PYTHONPATH=/app

RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        redis-server \
        libssl3 \
        python3 \
        python3-pip \
        python3-venv \
        python3-yaml \
        procps \
        pgrep \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv "${VIRTUAL_ENV}" && \
    "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip && \
    "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir 'huggingface_hub[cli]==1.5.0'

# Copy built router binary and libraries
COPY --from=build-go /build/router /usr/local/bin/router
COPY --from=build-rust-candle /build/out/libcandle_semantic_router.so /usr/local/lib/
COPY --from=build-ml /build/out/libml_semantic_router.so /usr/local/lib/
COPY --from=build-nlp /build/out/libnlp_binding.so /usr/local/lib/

# Copy Envoy binary
COPY --from=envoy /usr/local/bin/envoy /usr/local/bin/envoy

WORKDIR /app
RUN mkdir -p /app/.vllm-sr /app/config /app/models

# Copy startup script
COPY railway-start.sh /app/railway-start.sh
RUN chmod +x /app/railway-start.sh

# Copy vllm-sr CLI (Python package with CLI, templates, and helpers)
COPY src/vllm-sr/src/ /app/src/
COPY src/vllm-sr/cli/ /app/cli/
COPY src/vllm-sr/start-router.sh /app/start-router.sh
COPY src/vllm-sr/start-envoy.sh /app/start-envoy.sh
RUN chmod +x /app/start-router.sh /app/start-envoy.sh

# Copy knowledge bases and tools
COPY config/knowledge_bases/ /app/config/knowledge_bases/
COPY src/vllm-sr/cli/templates/ /app/cli/templates/
COPY src/vllm-sr/cli/templates/*.yaml /app/cli/templates/ 2>/dev/null || true
COPY src/vllm-sr/cli/templates/*.json /app/cli/templates/ 2>/dev/null || true

# Environment
ENV PORT=8899
ENV VLLM_SR_CONFIG=/app/config.yaml
ENV VLLM_SR_SETUP_MODE=false

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" 2>/dev/null || \
      curl -sf http://localhost:${PORT}/health || exit 1

EXPOSE 8899 8700 8080 6379

ENTRYPOINT ["/app/railway-start.sh"]
CMD []
