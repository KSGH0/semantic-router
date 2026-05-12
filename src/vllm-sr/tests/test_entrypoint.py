"""Tests for the Railway entrypoint (entrypoint.py)."""

import sys
import types
from pathlib import Path

import pytest

# Add repo root so ``from entrypoint import ...`` works.
# entrypoint.py lives at the repo root (next to Dockerfile).
# Test file: src/vllm-sr/tests/test_entrypoint.py → 4 levels up to repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
_ADDED = str(REPO_ROOT) not in sys.path
if _ADDED or True:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Helpers — build a fake ``cli.docker_runtime`` module
# ---------------------------------------------------------------------------


def _make_fake_docker_runtime():
    """Build a fake ``docker_runtime`` module that mirrors the real one."""
    from functools import lru_cache

    import logging

    mod = types.ModuleType("docker_runtime")
    mod.log = logging.getLogger("test.docker_runtime")

    @lru_cache(maxsize=1)
    def _detect():
        raise RuntimeError("original detect was called")

    mod._detect_container_runtime = _detect
    mod.get_container_runtime = lambda: mod._detect_container_runtime()

    return mod


def _install_fake_docker_runtime(monkeypatch):
    """Replace the real ``cli.docker_runtime`` with a fake in
    ``sys.modules`` so ``from cli import docker_runtime`` resolves to it.

    We must also delete ``docker_runtime`` from the ``cli`` package's
    namespace (``cli.__dict__``) — when other tests have already imported
    the real module, Python's import machinery short-circuits at
    ``getattr(cli, "docker_runtime")`` and never consults
    ``sys.modules["cli.docker_runtime"]``.
    """
    import cli as _cli_mod

    fake = _make_fake_docker_runtime()
    monkeypatch.delattr(_cli_mod, "docker_runtime", raising=False)
    monkeypatch.setitem(sys.modules, "cli.docker_runtime", fake)
    return fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_patch_replaces_both_functions(monkeypatch):
    """After patching, both ``_detect_container_runtime`` and
    ``get_container_runtime`` return ``"docker"`` unconditionally."""
    fake = _install_fake_docker_runtime(monkeypatch)

    from entrypoint import patch_docker_check

    patch_docker_check()

    assert fake._detect_container_runtime() == "docker"
    assert fake.get_container_runtime() == "docker"


def test_patch_clears_original_lru_cache(monkeypatch):
    """The original ``@lru_cache`` cache is cleared before replacement,
    so even a pre-warmed cache is irrelevant after patching."""
    fake = _install_fake_docker_runtime(monkeypatch)

    # Warm up the original cache (caches the RuntimeError)
    with pytest.raises(RuntimeError, match="original detect was called"):
        fake.get_container_runtime()

    from entrypoint import patch_docker_check

    patch_docker_check()

    # After patch the cached error is irrelevant
    assert fake._detect_container_runtime() == "docker"
    assert fake.get_container_runtime() == "docker"


def test_patch_handles_missing_cli(monkeypatch):
    """When ``cli`` module is not installed, patch logs a warning and
    does not crash."""
    # Remove the cli.docker_runtime module to simulate absence
    monkeypatch.delitem(sys.modules, "cli.docker_runtime", raising=False)

    from entrypoint import patch_docker_check

    # Should not raise — catches ImportError internally
    patch_docker_check()


# ---------------------------------------------------------------------------
# Argument forwarding (replicates the logic from entrypoint's __main__)
# ---------------------------------------------------------------------------


def _forward_args(argv):
    """Replicate the argv-forwarding logic from entrypoint.py ``__main__``."""
    passthrough = argv[1:]  # strip script path
    if passthrough and passthrough[0] == "vllm-sr":
        passthrough = passthrough[1:]
    return ["vllm-sr"] + passthrough


def test_forward_default_cmd():
    """python entrypoint.py vllm-sr serve  →  vllm-sr serve"""
    result = _forward_args(["/app/entrypoint.py", "vllm-sr", "serve"])
    assert result == ["vllm-sr", "serve"]


def test_forward_alternate_cmd():
    """python entrypoint.py status  →  vllm-sr status"""
    result = _forward_args(["/app/entrypoint.py", "status"])
    assert result == ["vllm-sr", "status"]


def test_forward_no_args():
    """python entrypoint.py  →  vllm-sr"""
    result = _forward_args(["/app/entrypoint.py"])
    assert result == ["vllm-sr"]


def test_forward_multiple_args_after_serve():
    """python entrypoint.py vllm-sr serve --port 9090  →  vllm-sr serve --port 9090"""
    result = _forward_args(
        ["/app/entrypoint.py", "vllm-sr", "serve", "--port", "9090"]
    )
    assert result == ["vllm-sr", "serve", "--port", "9090"]
