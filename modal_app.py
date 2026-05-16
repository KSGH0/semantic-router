"""
modal_app.py — Modal SDK deployment for vLLM Semantic Router

Two web-server functions built from source:
  • serve_router    — Go router + Envoy proxy  (GPU T4,  port 8899)
  • serve_dashboard — Dashboard backend + UI   (CPU,     port 8700)

Before first deploy, create the LLM API-key secret:
    modal secret create llm-api-keys \\
        OPENAI_API_KEY=sk-... \\
        ANTHROPIC_API_KEY=sk-ant-...

Deploy:
    modal deploy modal_app.py

Or via the interactive UI:
    python main.py
"""

import subprocess

import modal

app = modal.App("vllm-semantic-router")

# Router public URL — used by dashboard to reach the live router
_ROUTER_URL = "https://kswork38--vllm-semantic-router-serve-router.modal.run"

# ---------------------------------------------------------------------------
# Persistent volume — caches ML model weights across cold starts (~60 s saved)
# ---------------------------------------------------------------------------

model_volume = modal.Volume.from_name("vllm-sr-models", create_if_missing=True)

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

router_image = modal.Image.from_dockerfile("Dockerfile.modal", context_dir=".")
dashboard_image = modal.Image.from_dockerfile("dashboard/backend/Dockerfile", context_dir=".")

# ---------------------------------------------------------------------------
# Router  —  GPU T4, port 8899 (Envoy → Go router gRPC extproc)
# ---------------------------------------------------------------------------

@app.function(
    image=router_image,
    gpu="T4",
    # Scale to zero when idle — set min_containers=1 for always-warm (costs ~$0.59/hr per T4)
    min_containers=0,
    max_containers=4,
    # Scale down idle containers after 5 minutes; first request after idle takes ~2 min cold start
    scaledown_window=300,
    # 16 GB RAM — needed for Go router + ML classifiers + Envoy
    memory=16384,
    timeout=3600,
    # Persist ML model weights — avoids 60 s HuggingFace download on cold start
    volumes={"/app/models": model_volume},
    # LLM API keys forwarded to backend providers
    secrets=[modal.Secret.from_name("llm-api-keys", required_keys=[])],
)
@modal.concurrent(max_inputs=50)
@modal.web_server(8899, startup_timeout=180)
def serve_router():
    subprocess.Popen(["/app/start-modal.sh"])


# ---------------------------------------------------------------------------
# Dashboard  —  CPU only, port 8700
# ---------------------------------------------------------------------------

@app.function(
    image=dashboard_image,
    min_containers=0,
    max_containers=2,
    scaledown_window=300,
    memory=2048,
    timeout=3600,
    secrets=[
        modal.Secret.from_dict({
            "TARGET_ROUTER_API_URL": _ROUTER_URL,
            "TARGET_ENVOY_URL": _ROUTER_URL,
            "TARGET_ROUTER_METRICS_URL": f"{_ROUTER_URL}/metrics",
        })
    ],
)
@modal.concurrent(max_inputs=50)
@modal.web_server(8700, startup_timeout=60)
def serve_dashboard():
    subprocess.Popen([
        "/app/dashboard-backend",
        "-port=8700",
        "-static=/app/frontend",
        "-config=/app/config/config.yaml",
        f"-router_api={_ROUTER_URL}",
        f"-router_metrics={_ROUTER_URL}/metrics",
        f"-envoy={_ROUTER_URL}",
    ])
