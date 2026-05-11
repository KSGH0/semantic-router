# syntax=docker/dockerfile:1

# ─── Stage 1: Build Rust libraries ───────────────────────────────────────────
FROM rust:1.86-bookworm AS rust-builder

WORKDIR /app

# Copy only the Rust workspace manifests and lock files first for layer caching
COPY candle-binding/Cargo.toml candle-binding/Cargo.lock ./candle-binding/
COPY ml-binding/Cargo.toml ml-binding/Cargo.lock ./ml-binding/
COPY nlp-binding/Cargo.toml nlp-binding/Cargo.lock ./nlp-binding/

# Copy full source for each binding
COPY candle-binding/ ./candle-binding/
COPY ml-binding/ ./ml-binding/
COPY nlp-binding/ ./nlp-binding/

# Build all three Rust static libraries (CPU-only, no CUDA required)
RUN cd candle-binding && cargo build --release --no-default-features
RUN cd ml-binding && cargo build --release
RUN cd nlp-binding && cargo build --release

# ─── Stage 2: Build Go binary ─────────────────────────────────────────────────
FROM golang:1.24.1-bookworm AS go-builder

WORKDIR /app

# Copy the pre-built Rust static libraries so CGO can link against them
COPY --from=rust-builder /app/candle-binding/target/release/libcandle_semantic_router.a \
    ./candle-binding/target/release/
COPY --from=rust-builder /app/ml-binding/target/release/libml_semantic_router.a \
    ./ml-binding/target/release/
COPY --from=rust-builder /app/nlp-binding/target/release/libnlp_binding.a \
    ./nlp-binding/target/release/

# Copy the Go binding source (needed for CGO header resolution and go.mod replace directives)
COPY candle-binding/*.go ./candle-binding/
COPY candle-binding/go.mod ./candle-binding/
COPY ml-binding/*.go ./ml-binding/
COPY ml-binding/go.mod ./ml-binding/
COPY nlp-binding/*.go ./nlp-binding/
COPY nlp-binding/go.mod ./nlp-binding/

# Copy the semantic-router module
COPY src/semantic-router/ ./src/semantic-router/

# Download Go module dependencies from the semantic-router working directory.
# The replace directives in go.mod point to ../../candle-binding etc., which
# resolve correctly because the repo layout is preserved under /app.
WORKDIR /app/src/semantic-router
RUN go mod download

# Build the router binary with CGO enabled and the milvus build tag
RUN CGO_ENABLED=1 go build -tags=milvus -o /app/bin/router ./cmd

# ─── Stage 3: Minimal runtime image ──────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

# Install runtime dependencies: ca-certificates for TLS, and the C standard
# library components that the CGO binary links against at runtime (libdl, libm,
# libpthread are part of glibc which is already present in bookworm-slim).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy the compiled binary from the build stage
COPY --from=go-builder /app/bin/router /usr/local/bin/router

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/router"]
