#!/usr/bin/env python3
"""
add_provider.py — Add a new LLM provider to vLLM Semantic Router.

Automatically fetches all available models from any OpenAI-compatible API,
generates config entries, and stores the API key encrypted with AES-GCM.

Usage:
    python scripts/add_provider.py

Supported providers (auto-detected):
    OpenRouter  https://openrouter.ai/api/v1
    Groq        https://api.groq.com/openai/v1
    Together    https://api.together.xyz/v1
    Mistral     https://api.mistral.ai/v1
    Any OpenAI-compatible endpoint
"""

import base64
import getpass
import json
import os
import re
import sys
import subprocess
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "modal-config.yaml"
KEYS_FILE = ROOT / ".keys.enc"
COMPOSE_FILE = ROOT / "docker-compose.local.yml"

# ── Crypto helpers (AES-GCM via cryptography library) ─────────────────────────

def _get_crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        return AESGCM, PBKDF2HMAC, hashes
    except ImportError:
        print("Installing cryptography library...")
        subprocess.run([sys.executable, "-m", "pip", "install", "cryptography", "-q"], check=True)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        return AESGCM, PBKDF2HMAC, hashes


def _derive_key(password: str, salt: bytes) -> bytes:
    AESGCM, PBKDF2HMAC, hashes = _get_crypto()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return kdf.derive(password.encode())


def encrypt_key(plaintext: str, password: str) -> str:
    """Encrypt an API key with AES-GCM. Returns base64-encoded payload."""
    AESGCM, _, _ = _get_crypto()
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    payload = salt + nonce + ct
    return base64.b64encode(payload).decode()


def decrypt_key(encoded: str, password: str) -> str:
    """Decrypt an AES-GCM encrypted API key."""
    AESGCM, _, _ = _get_crypto()
    payload = base64.b64decode(encoded)
    salt, nonce, ct = payload[:16], payload[16:28], payload[28:]
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None).decode()


# ── Key store ─────────────────────────────────────────────────────────────────

def load_keystore() -> dict:
    if not KEYS_FILE.exists():
        return {}
    try:
        return json.loads(KEYS_FILE.read_text())
    except Exception:
        return {}


def save_keystore(store: dict) -> None:
    KEYS_FILE.write_text(json.dumps(store, indent=2))
    # Restrict file permissions
    try:
        os.chmod(str(KEYS_FILE), 0o600)
    except Exception:
        pass


def store_encrypted_key(env_var: str, api_key: str, password: str) -> None:
    store = load_keystore()
    store[env_var] = encrypt_key(api_key, password)
    save_keystore(store)
    print(f"  Key stored encrypted in {KEYS_FILE.name}")


def export_decrypted_env(password: str) -> dict[str, str]:
    """Decrypt all stored keys and return as env var dict."""
    store = load_keystore()
    result = {}
    for env_var, encrypted in store.items():
        try:
            result[env_var] = decrypt_key(encrypted, password)
        except Exception:
            print(f"  Warning: could not decrypt {env_var} (wrong password?)")
    return result


# ── Provider detection ────────────────────────────────────────────────────────

KNOWN_PROVIDERS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "name": "OpenRouter",
        "env_var": "OPENROUTER_API_KEY",
        "chat_path": "/api/v1/chat/completions",
        "host": "openrouter.ai",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "name": "Groq",
        "env_var": "GROQ_API_KEY",
        "chat_path": "/openai/v1/chat/completions",
        "host": "api.groq.com",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "name": "Together AI",
        "env_var": "TOGETHER_API_KEY",
        "chat_path": "/v1/chat/completions",
        "host": "api.together.xyz",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "name": "Mistral AI",
        "env_var": "MISTRAL_API_KEY",
        "chat_path": "/v1/chat/completions",
        "host": "api.mistral.ai",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "name": "OpenAI",
        "env_var": "OPENAI_API_KEY",
        "chat_path": "/v1/chat/completions",
        "host": "api.openai.com",
    },
}


def detect_provider(base_url: str) -> dict:
    for key, info in KNOWN_PROVIDERS.items():
        if key in base_url.lower() or info["host"] in base_url.lower():
            return info
    # Custom provider
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    name = parsed.hostname or "custom"
    env_var = re.sub(r"[^A-Z0-9]", "_", name.upper()) + "_API_KEY"
    return {
        "base_url": base_url,
        "name": name,
        "env_var": env_var,
        "chat_path": "/v1/chat/completions",
        "host": parsed.hostname,
    }


# ── Model fetching ────────────────────────────────────────────────────────────

def fetch_models(base_url: str, api_key: str) -> list[dict]:
    import urllib.request
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return data.get("data", data) if isinstance(data, dict) else data
    except Exception as e:
        print(f"  Could not fetch models: {e}")
        return []


def guess_capabilities(model_id: str, description: str = "") -> list[str]:
    text = (model_id + " " + description).lower()
    caps = ["chat"]
    if any(x in text for x in ["vision", "image", "visual", "vl", "omni"]):
        caps.append("vision")
    if any(x in text for x in ["code", "coder", "codex", "starcoder", "deepseek-coder"]):
        caps.append("tools")
    if any(x in text for x in ["reasoning", "think", "r1", "o1", "o3", "preview"]):
        caps.append("reasoning")
    if any(x in text for x in ["128k", "200k", "long", "context"]):
        caps.append("long-context")
    if "tools" not in caps:
        caps.append("tools")
    return caps


def guess_quality(model_id: str) -> float:
    mid = model_id.lower()
    if any(x in mid for x in ["opus", "4o", "gpt-4", "sonnet", "70b", "72b", "405b"]):
        return 0.92
    if any(x in mid for x in ["haiku", "mini", "flash", "8b", "7b", "small"]):
        return 0.78
    return 0.85


def guess_param_size(model_id: str) -> str:
    match = re.search(r"(\d+\.?\d*)b", model_id.lower())
    if match:
        return f"{match.group(1)}B"
    if any(x in model_id.lower() for x in ["large", "opus", "gpt-4"]):
        return "100B+"
    if any(x in model_id.lower() for x in ["small", "mini", "haiku", "flash"]):
        return "8B"
    return "Unknown"


def model_to_config(model: dict, provider: dict) -> tuple[str, str]:
    """Generate providers.models entry and routing.modelCards entry."""
    mid = model.get("id", "")
    ctx = model.get("context_length", 8192)
    desc = model.get("description", f"{mid} via {provider['name']}")[:120]
    pricing = model.get("pricing", {})

    prompt_price = float(pricing.get("prompt", "0")) * 1_000_000
    completion_price = float(pricing.get("completion", "0")) * 1_000_000

    caps = guess_capabilities(mid, desc)
    quality = guess_quality(mid)
    param_size = guess_param_size(mid)
    tags = []
    if quality >= 0.90:
        tags.append("premium")
    if quality <= 0.80:
        tags.append("fast")
    if "vision" in caps:
        tags.append("vision")
    if "open" in mid.lower() or "llama" in mid.lower() or "mistral" in mid.lower():
        tags.append("open-source")

    provider_entry = f"""    - name: {mid}
      provider_model_id: {mid}
      api_format: openai
      pricing:
        currency: USD
        prompt_per_1m: {prompt_price:.4f}
        completion_per_1m: {completion_price:.4f}
      external_model_ids:
        openai: {mid}
      backend_refs:
        - name: {provider['name'].lower().replace(' ', '-')}
          base_url: {provider['base_url']}
          provider: openai
          auth_header: Authorization
          auth_prefix: Bearer
          chat_path: {provider['chat_path']}
          api_key_env: {provider['env_var']}
          weight: 100"""

    model_card = f"""    - name: {mid}
      param_size: {param_size}
      context_window_size: {ctx}
      description: {desc}
      capabilities: [{', '.join(caps)}]
      quality_score: {quality}
      modality: ar
      tags: [{', '.join(tags) if tags else 'general'}]"""

    return provider_entry, model_card


# ── Config patching ───────────────────────────────────────────────────────────

def patch_config(provider_entries: list[str], model_cards: list[str]) -> None:
    content = CONFIG_FILE.read_text(encoding="utf-8")

    # Add to providers.models section
    provider_block = "\n\n".join(provider_entries)
    if "backend_refs:" in content:
        # Find last backend_refs block and insert after it
        last_backend = content.rfind("          weight: 100")
        if last_backend != -1:
            end = content.find("\n", last_backend) + 1
            content = content[:end] + "\n" + provider_block + "\n" + content[end:]

    # Add to routing.modelCards section
    card_block = "\n\n".join(model_cards)
    if "modelCards:" in content:
        last_card = content.rfind("      tags:")
        if last_card != -1:
            end = content.find("\n\n", last_card)
            if end == -1:
                end = content.find("\n  signals:", last_card)
            if end != -1:
                content = content[:end] + "\n\n" + card_block + content[end:]

    CONFIG_FILE.write_text(content, encoding="utf-8")


def update_docker_compose_env(env_var: str) -> None:
    content = COMPOSE_FILE.read_text(encoding="utf-8")
    if env_var in content:
        return  # Already there
    # Find router environment section
    marker = "      HF_TOKEN: ${HF_TOKEN:-}"
    if marker in content:
        insert = f"      {env_var}: ${{{env_var}:-}}\n"
        content = content.replace(marker, insert + marker)
        COMPOSE_FILE.write_text(content, encoding="utf-8")
        print(f"  Added {env_var} to docker-compose router environment")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  vLLM Semantic Router — Add Provider")
    print("=" * 60)
    print()

    # Show known providers
    print("Known providers:")
    for i, (k, v) in enumerate(KNOWN_PROVIDERS.items(), 1):
        print(f"  {i}. {v['name']} ({v['base_url']})")
    print(f"  {len(KNOWN_PROVIDERS)+1}. Custom provider")
    print()

    choice = input("Select provider (1-6) or press Enter for OpenRouter: ").strip()
    keys = list(KNOWN_PROVIDERS.keys())

    if not choice or choice == "1":
        provider = KNOWN_PROVIDERS["openrouter"]
    elif choice.isdigit() and 1 <= int(choice) <= len(keys):
        provider = KNOWN_PROVIDERS[keys[int(choice) - 1]]
    else:
        base_url = input("Custom provider base URL: ").strip()
        provider = detect_provider(base_url)

    print(f"\n  Provider: {provider['name']}")
    print(f"  Env var : {provider['env_var']}")
    print()

    # API key
    api_key = getpass.getpass(f"  {provider['name']} API key: ").strip()
    if not api_key:
        print("  No key entered. Exiting.")
        return

    # Master password for encryption
    print()
    print("  API key will be encrypted with AES-GCM.")
    while True:
        password = getpass.getpass("  Master password (min 8 chars): ").strip()
        if len(password) >= 8:
            break
        print("  Password too short.")
    confirm = getpass.getpass("  Confirm password: ").strip()
    if password != confirm:
        print("  Passwords do not match. Exiting.")
        return

    # Fetch models
    print(f"\n  Fetching models from {provider['name']}...")
    models = fetch_models(provider["base_url"], api_key)

    if not models:
        print("  No models found. Check your API key and provider URL.")
        return

    # Filter to chat models only
    chat_models = [m for m in models if not str(m.get("id", "")).startswith("~")]
    print(f"  Found {len(chat_models)} models.")
    print()

    # Let user filter
    print("  Options:")
    print("  A. Add ALL models automatically")
    print("  F. Filter by keyword (e.g. 'llama', 'gpt', 'claude')")
    print("  S. Select individually")
    mode = input("  Choice [A/F/S]: ").strip().upper() or "A"

    selected = []
    if mode == "A":
        selected = chat_models
    elif mode == "F":
        kw = input("  Keyword filter: ").strip().lower()
        selected = [m for m in chat_models if kw in m.get("id", "").lower()]
        print(f"  Matched {len(selected)} models.")
    else:
        print()
        for i, m in enumerate(chat_models[:50], 1):
            print(f"  {i:3}. {m.get('id', '?')}")
        if len(chat_models) > 50:
            print(f"  ... and {len(chat_models)-50} more")
        picks = input("\n  Enter numbers (comma-separated) or 'all': ").strip()
        if picks.lower() == "all":
            selected = chat_models
        else:
            idxs = [int(x.strip()) - 1 for x in picks.split(",") if x.strip().isdigit()]
            selected = [chat_models[i] for i in idxs if 0 <= i < len(chat_models)]

    if not selected:
        print("  No models selected.")
        return

    print(f"\n  Adding {len(selected)} models to config...")

    # Generate config entries
    provider_entries, model_cards = [], []
    for m in selected:
        pe, mc = model_to_config(m, provider)
        provider_entries.append(pe)
        model_cards.append(mc)

    # Patch config file
    patch_config(provider_entries, model_cards)
    print(f"  Updated {CONFIG_FILE.name}")

    # Encrypt and store API key
    store_encrypted_key(provider["env_var"], api_key, password)

    # Update docker-compose
    update_docker_compose_env(provider["env_var"])

    # Write decrypted key to .env for current session
    env_path = ROOT / ".env"
    env_content = env_path.read_text() if env_path.exists() else ""
    env_var_line = f"{provider['env_var']}={api_key}"
    if provider["env_var"] not in env_content:
        with open(env_path, "a") as f:
            f.write(f"\n{env_var_line}\n")
    else:
        lines = env_content.splitlines()
        lines = [l if not l.startswith(provider["env_var"] + "=") else env_var_line for l in lines]
        env_path.write_text("\n".join(lines) + "\n")

    print()
    print("=" * 60)
    print(f"  Done! Added {len(selected)} models from {provider['name']}.")
    print()
    print("  Next steps:")
    print("    1. Restart the router:")
    print("       docker compose -f docker-compose.local.yml --env-file .env restart router")
    print()
    print("  To decrypt your keys later:")
    print("    python scripts/add_provider.py --export-env")
    print("=" * 60)


def export_env_cmd():
    """Print decrypted env vars for shell export."""
    password = getpass.getpass("Master password: ").strip()
    keys = export_decrypted_env(password)
    if not keys:
        print("No keys found or wrong password.")
        return
    for var, val in keys.items():
        print(f"export {var}={val}")


if __name__ == "__main__":
    if "--export-env" in sys.argv:
        export_env_cmd()
    else:
        main()
