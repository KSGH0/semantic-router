#!/usr/bin/env python3
"""
main.py — vLLM Semantic Router · Modal UI

Interactive CLI for Modal token configuration, project management, and
deployment of the vLLM Semantic Router services (router + dashboard).

Usage:
    python main.py                # Interactive menu
    python main.py --check        # Run preflight checks only
    python main.py --deploy-router --project project-alpha
    python main.py --status

Credentials are stored securely in ~/.modal-config.json (outside the
repository).  All Modal operations are delegated to the official
``modal`` CLI via subprocess calls.
"""

import argparse
import getpass
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any

# ---------------------------------------------------------------------------
# UTF-8 output — ensures emoji display correctly on Windows (cp1252 default)
# ---------------------------------------------------------------------------
if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace",
        )
if sys.stderr.encoding and sys.stderr.encoding.upper() not in ("UTF-8", "UTF8"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace",
        )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PYTHON_INTERPRETER = Path(
    r"C:\Users\darsh\AppData\Local\Programs\Python\Python312\python.exe"
)

CONFIG_FILE_NAME = ".modal-config.json"
CONFIG_PATH = Path.home() / CONFIG_FILE_NAME

ENV_MODAL_TOKEN_ID = "MODAL_TOKEN_ID"
ENV_MODAL_TOKEN_SECRET = "MODAL_TOKEN_SECRET"

MODAL_APP_NAME = "vllm-semantic-router"
ROUTER_APP_NAME = "vllm-semantic-router"      # kept for backwards-compat display
DASHBOARD_APP_NAME = "vllm-semantic-router"   # same app, different function
DEFAULT_PROJECT = "main"
ALTERNATE_PROJECT = "dev"

# modal_app.py is the Modal SDK deployment definition
MODAL_APP_FILE = Path(__file__).resolve().parent / "modal_app.py"
MODAL_JSON_PATH = Path(__file__).resolve().parent / "modal.json"


# ---------------------------------------------------------------------------
# Config persistence (atomic writes, restricted permissions)
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, str]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _lock_permissions(path_str: str) -> None:
    try:
        if os.name == "posix":
            os.chmod(path_str, stat.S_IRUSR | stat.S_IWUSR)
        elif os.name == "nt":
            owner = os.environ.get("USERNAME", "")
            if owner:
                subprocess.run(
                    ["icacls", path_str, "/inheritance:r", "/grant", f"{owner}:F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
    except Exception:
        pass


def _save_config(token_id: str, token_secret: str) -> None:
    data = {"modal_token_id": token_id, "modal_token_secret": token_secret}
    fd, tmp_path_str = tempfile.mkstemp(
        suffix=".tmp", prefix="modal-config-", dir=str(CONFIG_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        _lock_permissions(tmp_path_str)
        shutil.move(tmp_path_str, str(CONFIG_PATH))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
    _lock_permissions(str(CONFIG_PATH))


# ---------------------------------------------------------------------------
# Sensitive-data clearing (best-effort memory overwrite)
# ---------------------------------------------------------------------------

def _clear_sensitive(*secrets: str) -> None:
    for s in secrets:
        try:
            data = bytearray(s.encode("utf-8"))
            for i in range(len(data)):
                data[i] = 0
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Modal CLI interaction
# ---------------------------------------------------------------------------

def _build_env(token_id: str, token_secret: str) -> Dict[str, str]:
    env = os.environ.copy()
    env[ENV_MODAL_TOKEN_ID] = token_id
    env[ENV_MODAL_TOKEN_SECRET] = token_secret
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _modal_cmd(args: List[str], token_id: str, token_secret: str) -> subprocess.CompletedProcess:
    cmd = [str(PYTHON_INTERPRETER), "-m", "modal"] + args
    env = _build_env(token_id, token_secret)
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)


def _check_result(
    result: subprocess.CompletedProcess,
    ok_label: str = "Succeeded",
    fail_label: str = "Failed",
    show_output: bool = True,
) -> bool:
    if result.returncode == 0:
        print(f"  ✅ {ok_label}")
        if show_output and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f"     {line}")
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines():
                print(f"     (stderr) {line}")
        return True
    else:
        print(f"  ❌ {fail_label} (exit code {result.returncode})")
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines():
                print(f"     Error: {line}")
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f"     Output: {line}")
        return False


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

def _check_python() -> Optional[str]:
    if not os.path.isfile(str(PYTHON_INTERPRETER)):
        return (
            f"Python interpreter not found:\n"
            f"  {PYTHON_INTERPRETER}\n"
            f"Please verify the path or install Python 3.12."
        )
    try:
        result = subprocess.run(
            [str(PYTHON_INTERPRETER), "--version"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return (
                f"Python interpreter at {PYTHON_INTERPRETER} "
                f"is not working:\n  {result.stderr.strip()}"
            )
    except Exception as exc:
        return f"Could not run Python interpreter: {exc}"
    return None


def _check_modal_installed() -> Optional[str]:
    try:
        result = subprocess.run(
            [str(PYTHON_INTERPRETER), "-m", "modal", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return (
                f"The Modal CLI is not installed for {PYTHON_INTERPRETER}.\n"
                f"Install it with:\n"
                f"  {PYTHON_INTERPRETER} -m pip install modal"
            )
    except Exception as exc:
        return f"Could not check Modal installation: {exc}"
    return None


def _check_modal_app() -> Optional[str]:
    if not MODAL_APP_FILE.is_file():
        return f"modal_app.py not found at {MODAL_APP_FILE} — run setup first."
    return None


def _check_modal_json() -> Optional[Dict[str, Any]]:
    if not os.path.isfile(str(MODAL_JSON_PATH)):
        return None
    try:
        with open(MODAL_JSON_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _get_credentials() -> Optional[Dict[str, str]]:
    config = _load_config()
    tid = config.get("modal_token_id", "")
    tsec = config.get("modal_token_secret", "")
    if not tid or not tsec:
        return None
    return {"token_id": tid, "token_secret": tsec}


def _require_credentials() -> Optional[Dict[str, str]]:
    creds = _get_credentials()
    if creds is None:
        print("\n  ❌ No token configured. Please use option 1 first.")
    return creds


# ---------------------------------------------------------------------------
# Action: Configure token
# ---------------------------------------------------------------------------

def action_configure() -> None:
    print()
    print("=" * 60)
    print("  Configure Modal Token")
    print("=" * 60)

    existing = _load_config()
    if existing.get("modal_token_id"):
        masked = existing["modal_token_id"][:8]
        print(f"\n  Existing token ID found: {masked}...")
        choice = input("  Overwrite? (y/N): ").strip().lower()
        if choice != "y":
            print("  Configuration cancelled.")
            return

    print("\n  Enter your Modal credentials.")
    print("  (Find these at https://modal.com/settings)")
    print()

    token_id = input("  Modal Token ID: ").strip()
    if not token_id:
        print("  Token ID cannot be empty. Aborting.")
        return

    token_secret = getpass.getpass("  Modal Token Secret: ").strip()
    if not token_secret:
        print("  Token Secret cannot be empty. Aborting.")
        _clear_sensitive(token_id)
        return

    try:
        _save_config(token_id, token_secret)
        print(f"\n  ✅ Configuration saved to {CONFIG_PATH}")
    except Exception as exc:
        print(f"\n  ❌ Failed to save configuration: {exc}")
        _clear_sensitive(token_id, token_secret)
        return

    _clear_sensitive(token_secret)

    print()
    test_choice = input("  Test the token now? (Y/n): ").strip().lower()
    if test_choice != "n":
        action_test_token(token_id, token_secret)

    _clear_sensitive(token_id, token_secret)


# ---------------------------------------------------------------------------
# Action: Test token
# ---------------------------------------------------------------------------

def action_test_token(
    token_id: Optional[str] = None,
    token_secret: Optional[str] = None,
) -> None:
    print()
    print("=" * 60)
    print("  Test Modal Token")
    print("=" * 60)

    if not token_id or not token_secret:
        creds = _require_credentials()
        if creds is None:
            return
        token_id = creds["token_id"]
        token_secret = creds["token_secret"]

    print("\n  Running `modal token list` to validate credentials...")
    try:
        result = _modal_cmd(["token", "list"], token_id, token_secret)
    except subprocess.TimeoutExpired:
        print("\n  ❌ Command timed out after 180 seconds.")
        return
    except Exception as exc:
        print(f"\n  ❌ Failed to run modal CLI: {exc}")
        return

    _check_result(result, ok_label="Token is valid", fail_label="Token validation failed")
    _clear_sensitive(token_secret)


# ---------------------------------------------------------------------------
# Action: List projects
# ---------------------------------------------------------------------------

def action_list_projects(token_id: str, token_secret: str) -> None:
    print("\n  Fetching project list...")
    try:
        result = _modal_cmd(["project", "list"], token_id, token_secret)
    except subprocess.TimeoutExpired:
        print("  ❌ Command timed out after 180 seconds.")
        return
    except Exception as exc:
        print(f"  ❌ Failed to list projects: {exc}")
        return

    if result.returncode == 0:
        output = result.stdout.strip()
        if output:
            print("\n  📦 Existing projects:")
            for line in output.splitlines():
                print(f"     {line}")
        else:
            print("\n  (No projects found.)")
    else:
        print(f"\n  ❌ Failed to list projects (exit code {result.returncode}).")
        if result.stderr.strip():
            print(f"     Error: {result.stderr.strip()}")

    _clear_sensitive(token_secret)


# ---------------------------------------------------------------------------
# Action: Create project
# ---------------------------------------------------------------------------

def action_create_project(token_id: str, token_secret: str) -> None:
    print()
    print("=" * 60)
    print("  Create New Modal Project")
    print("=" * 60)
    print("  (A Modal project is a logical grouping of apps and environments.)")

    name = input("\n  Project name: ").strip()
    if not name:
        print("  Project name cannot be empty. Cancelled.")
        return

    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", name):
        print(
            "  ⚠️  Project name should start with a letter or digit and contain\n"
            "     only letters, digits, hyphens, or underscores."
        )
        confirm = input("  Create anyway? (y/N): ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return

    region = input("  Region [us-east]: ").strip() or "us-east"
    plan = input("  Plan [scale]: ").strip() or "scale"

    print(f"\n  Creating project '{name}' (region={region}, plan={plan})...")
    try:
        result = _modal_cmd(
            ["project", "create", name, "--region", region, "--plan", plan],
            token_id, token_secret,
        )
    except subprocess.TimeoutExpired:
        print("  ❌ Command timed out after 180 seconds.")
        return
    except Exception as exc:
        print(f"  ❌ Failed to create project: {exc}")
        return

    _check_result(result, ok_label=f"Project '{name}' created", fail_label="Failed to create project")
    _clear_sensitive(token_secret)


# ---------------------------------------------------------------------------
# Action: Deploy (both services via modal_app.py)
# ---------------------------------------------------------------------------

def action_create_secrets(token_id: str, token_secret: str) -> None:
    """Create the llm-api-keys Modal Secret for LLM provider credentials."""
    print()
    print("=" * 60)
    print("  Configure LLM API Keys (Modal Secret)")
    print("=" * 60)
    print("  These keys are forwarded by the router to OpenAI / Anthropic.")
    print()

    openai_key = input("  OpenAI API key (Enter to skip): ").strip()
    anthropic_key = input("  Anthropic API key (Enter to skip): ").strip()

    if not openai_key and not anthropic_key:
        print("  No keys entered. Cancelled.")
        return

    args = ["secret", "create", "llm-api-keys", "--force"]
    if openai_key:
        args.append(f"OPENAI_API_KEY={openai_key}")
    if anthropic_key:
        args.append(f"ANTHROPIC_API_KEY={anthropic_key}")

    print("\n  Creating Modal Secret 'llm-api-keys'...")
    try:
        result = _modal_cmd(args, token_id, token_secret)
    except subprocess.TimeoutExpired:
        print("  ❌ Timed out.")
        return
    except Exception as exc:
        print(f"  ❌ Failed: {exc}")
        return

    _check_result(result, ok_label="Secret created", fail_label="Failed to create secret")
    _clear_sensitive(token_secret, openai_key, anthropic_key)


def action_deploy_router(
    token_id: str,
    token_secret: str,
    project: Optional[str] = None,
) -> None:
    print()
    print("=" * 60)
    print("  Deploy Services (router + dashboard)")
    print("=" * 60)

    app_err = _check_modal_app()
    if app_err:
        print(f"\n  ❌ {app_err}")
        return

    if project is None:
        project = input(f"  Target project [{DEFAULT_PROJECT}]: ").strip() or DEFAULT_PROJECT

    # Stop the running deployment first so no two versions overlap
    print(f"\n  Stopping current deployment of '{MODAL_APP_NAME}'...")
    try:
        _modal_cmd(["app", "stop", MODAL_APP_NAME, "--env", project, "--yes"], token_id, token_secret)
    except Exception:
        pass  # nothing running yet is fine

    print(f"  Deploying via {MODAL_APP_FILE.name}...")
    print(f"  (This may take several minutes.)\n")

    try:
        result = _modal_cmd(
            ["deploy", str(MODAL_APP_FILE), "--env", project],
            token_id, token_secret,
        )
    except subprocess.TimeoutExpired:
        print("  ❌ Deployment timed out after 180 seconds.")
        return
    except Exception as exc:
        print(f"  ❌ Deployment failed: {exc}")
        return

    if _check_result(result, ok_label="Deployed", fail_label="Deployment failed"):
        workspace = "kswork38"
        base = f"https://{workspace}--{MODAL_APP_NAME}"
        print(f"\n  🔗 Router    : {base}-serve-router.modal.run")
        print(f"  🔗 Dashboard : {base}-serve-dashboard.modal.run")

    _clear_sensitive(token_secret)


def action_deploy_dashboard(
    token_id: str,
    token_secret: str,
    project: Optional[str] = None,
) -> None:
    """Alias — modal_app.py deploys both services together."""
    action_deploy_router(token_id, token_secret, project=project)


# ---------------------------------------------------------------------------
# Action: Project management sub-menu
# ---------------------------------------------------------------------------

def action_project_menu() -> None:
    creds = _require_credentials()
    if creds is None:
        return

    while True:
        print()
        print("─" * 40)
        print("  Project Management")
        print("─" * 40)
        print("  1. List projects")
        print("  2. Create a new project")
        print("  0. Back to main menu")
        print()

        choice = input("  Choice: ").strip()

        if choice == "0":
            break
        elif choice == "1":
            action_list_projects(creds["token_id"], creds["token_secret"])
        elif choice == "2":
            action_create_project(creds["token_id"], creds["token_secret"])
        else:
            print("  Invalid choice. Please try again.")

    _clear_sensitive(creds["token_secret"])


# ---------------------------------------------------------------------------
# Action: View status
# ---------------------------------------------------------------------------

def action_view_status() -> None:
    print()
    print("=" * 60)
    print("  Status Overview")
    print("=" * 60)

    py_ok = _check_python() is None
    print(f"\n  Python Interpreter:")
    print(f"    Path   : {PYTHON_INTERPRETER}")
    print(f"    Status : {'✅ Available' if py_ok else '❌ Not found'}")

    modal_ok = _check_modal_installed() is None
    print(f"\n  Modal CLI:")
    print(f"    Status : {'✅ Installed' if modal_ok else '❌ Not installed'}")

    app_ok = _check_modal_app() is None
    print(f"\n  Modal App Definition:")
    print(f"    File   : {MODAL_APP_FILE}")
    print(f"    Status : {'✅ Found' if app_ok else '❌ Missing (create modal_app.py)'}")

    creds = _get_credentials()
    if creds:
        masked = creds["token_id"][:8]
        print(f"\n  Credentials:")
        print(f"    Token ID : {masked}...")
        print(f"    File     : {CONFIG_PATH}")
    else:
        print(f"\n  Credentials:")
        print(f"    Status   : ❌ Not configured")
        print(f"    File     : {CONFIG_PATH}")

    modal_json = _check_modal_json()
    if modal_json:
        projects = modal_json.get("projects", [])
        apps = modal_json.get("apps", [])
        print(f"\n  Project Configuration (modal.json):")
        if projects:
            print(f"    Projects : {', '.join(p.get('name', '?') for p in projects)}")
        if apps:
            print(f"    Apps     : {', '.join(a.get('name', '?') for a in apps)}")

    print()
    _clear_sensitive(creds["token_secret"] if creds else "")


# ---------------------------------------------------------------------------
# Main interactive menu
# ---------------------------------------------------------------------------

def main_menu() -> None:
    while True:
        config = _load_config()
        has_token = bool(config.get("modal_token_id"))

        print()
        print("=" * 60)
        print("  vLLM Semantic Router — Modal UI")
        print("=" * 60)
        print(f"  Config : {CONFIG_PATH}")
        print(f"  Token  : {'✅ Configured' if has_token else '❌ Not configured'}")
        print(f"  App    : {'✅ modal_app.py found' if MODAL_APP_FILE.is_file() else '❌ modal_app.py missing'}")
        print("=" * 60)
        print()
        print("  1. Configure / update Modal token")
        print("  2. Test token")
        print("  3. List / create projects")
        if has_token:
            print("  4. Deploy services (router + dashboard)")
            print("  5. Configure LLM API keys (Modal Secret)")
            print("  6. Stop deployment (save credits)")
        print("  7. View status")
        print()
        print("  0. Exit")
        print()

        choice = input("  Choice: ").strip()

        if choice == "0":
            print("\n  Goodbye!")
            _clear_sensitive(config.get("modal_token_secret", ""))
            break
        elif choice == "1":
            action_configure()
        elif choice == "2":
            action_test_token()
        elif choice == "3":
            action_project_menu()
        elif choice == "4" and has_token:
            action_deploy_router(config["modal_token_id"], config["modal_token_secret"])
        elif choice == "5" and has_token:
            action_create_secrets(config["modal_token_id"], config["modal_token_secret"])
        elif choice == "6" and has_token:
            creds = _get_credentials()
            if creds:
                print(f"\n  Stopping '{MODAL_APP_NAME}'...")
                r = _modal_cmd(["app", "stop", MODAL_APP_NAME, "--yes"], creds["token_id"], creds["token_secret"])
                _check_result(r, ok_label="Stopped — no containers running", fail_label="Stop failed")
                _clear_sensitive(creds["token_secret"])
        elif choice == "7":
            action_view_status()
        else:
            print("\n  Invalid choice. Please try again.")

        _clear_sensitive(config.get("modal_token_secret", ""))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def run_preflight_checks() -> List[str]:
    errors: List[str] = []
    py_err = _check_python()
    if py_err:
        errors.append(py_err)
    modal_err = _check_modal_installed()
    if modal_err:
        errors.append(modal_err)
    return errors


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="vLLM Semantic Router — Modal UI (token config + deploy).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py                        Interactive menu\n"
            "  python main.py --check                Preflight checks only\n"
            "  python main.py --deploy-router        Deploy both services\n"
            "  python main.py --status               Status overview"
        ),
    )
    parser.add_argument("--check", action="store_true",
                        help="Run preflight checks only.")
    parser.add_argument("--deploy-router", action="store_true",
                        help="Deploy both services non-interactively (requires token).")
    parser.add_argument("--deploy-dashboard", action="store_true",
                        help="Alias for --deploy-router.")
    parser.add_argument("--status", action="store_true",
                        help="Show status overview (non-interactive).")
    parser.add_argument("--project", type=str, default=None,
                        help=f"Target Modal environment/project (default: {DEFAULT_PROJECT}).")
    parser.add_argument("--token-id", type=str, default=None,
                        help="Modal token ID (skips stored config; use with --token-secret).")
    parser.add_argument("--token-secret", type=str, default=None,
                        help="Modal token secret (skips stored config; use with --token-id).")
    return parser


parser = _build_parser()


def main() -> None:
    args = parser.parse_args()
    errors = run_preflight_checks()

    if args.check:
        print("=" * 60)
        print("  Preflight Checks — vLLM Semantic Router Modal UI")
        print("=" * 60)
        if errors:
            for e in errors:
                print(f"  ❌ {e}")
            print(f"\n  ❌ Some checks failed.")
            sys.exit(1)
        else:
            print(f"  ✅ Python : {PYTHON_INTERPRETER}")
            print(f"  ✅ Modal  : Installed")
            app_err = _check_modal_app()
            if app_err:
                print(f"  ⚠️  App    : {app_err}")
            else:
                print(f"  ✅ App    : {MODAL_APP_FILE}")
            print(f"\n  ✅ All checks passed.")
            return

    # Inline credentials take priority over stored config
    if args.token_id and args.token_secret:
        creds = {"token_id": args.token_id, "token_secret": args.token_secret}
    else:
        creds = _get_credentials()

    if args.deploy_router or args.deploy_dashboard:
        if errors:
            print("\n❌ Preflight check(s) failed — cannot proceed.\n")
            for e in errors:
                print(f"  • {e}")
            sys.exit(1)
        if creds is None:
            print(
                "\n❌ No token configured.\n"
                "  Pass credentials via --token-id / --token-secret, or run\n"
                "  `python main.py` interactively to store them."
            )
            sys.exit(1)
        project = args.project or DEFAULT_PROJECT
        action_deploy_router(creds["token_id"], creds["token_secret"], project=project)
        _clear_sensitive(creds["token_secret"])
        return

    if args.status:
        action_view_status()
        return

    if errors:
        print("\n⚠️  Preflight check(s) failed:\n")
        for e in errors:
            print(f"  • {e}")
        print()
        print("=" * 60)
        print("  WARNING: Proceeding may not work correctly.")
        print("=" * 60)
        proceed = input("\n  Proceed anyway? (y/N): ").strip().lower()
        if proceed != "y":
            print("  Exiting.")
            sys.exit(1)

    main_menu()


if __name__ == "__main__":
    main()
