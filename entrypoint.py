#!/usr/bin/env python3
"""Entrypoint for Railway deployment - patches Docker availability check."""
import sys


def patch_docker_check():
    """Patch vllm-sr Docker check to allow running without Docker.

    Replaces ``_detect_container_runtime`` and ``get_container_runtime``
    with a no-op that always returns ``"docker"``.  This lets the container
    start successfully when no Docker daemon is available (e.g. Railway /
    serverless platforms).
    """
    try:
        from cli import docker_runtime

        def _patched_runtime():
            docker_runtime.log.info(
                "Container runtime: using in-process mode "
                "(Docker check patched for Railway)"
            )
            return "docker"

        # Clear any previously cached result from the original function
        # before replacing it with the patched version.
        docker_runtime._detect_container_runtime.cache_clear()

        # Replace the cached detection function
        docker_runtime._detect_container_runtime = _patched_runtime

        # Replace the public API entry point
        docker_runtime.get_container_runtime = _patched_runtime

        print("Docker check patched successfully")
    except ImportError as e:
        print(f"Warning: Could not patch Docker check: {e}", file=sys.stderr)
        print(
            "vllm-sr may not be installed yet. "
            "Continuing with normal startup.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    # Apply monkeypatch BEFORE importing vllm-sr CLI logic
    patch_docker_check()

    # Execute vllm-sr CLI in-process so the monkeypatches remain active.
    from cli.main import main

    # Docker combines ENTRYPOINT + CMD:
    #   python /app/entrypoint.py vllm-sr serve
    # The "vllm-sr" token is an artifact of the default CMD.
    # Strip it, then rebuild sys.argv for Click.
    passthrough_args = sys.argv[1:]  # remove entrypoint.py path
    if passthrough_args and passthrough_args[0] == "vllm-sr":
        passthrough_args = passthrough_args[1:]

    sys.argv = ["vllm-sr"] + passthrough_args
    sys.exit(main())
