#!/usr/bin/env python3
"""Entrypoint for Railway deployment - patches Docker availability check."""
import os
import stat
import sys


def create_fake_docker():
    """Create a fake docker binary that exits 0."""
    fake_docker = "#!/bin/sh\nexit 0\n"
    docker_path = "/usr/local/bin/docker"
    try:
        with open(docker_path, "w") as f:
            f.write(fake_docker)
        os.chmod(docker_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        print("Created fake docker binary")
    except Exception as e:
        print(f"Warning: Could not create fake docker: {e}")


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
    # Create fake docker binary BEFORE applying monkeypatches
    create_fake_docker()

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
