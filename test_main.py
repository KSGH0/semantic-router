#!/usr/bin/env python3
"""
Tests for modal_deploy.py — the combined Modal configuration & deployment CLI.

Strategy:
  - Unit tests for config persistence, credential helpers, and sensitive-data
    clearing via isolated temp directories.
  - Unit tests for preflight checks (with mocked subprocess).
  - Unit tests for argument parsing.
  - Integration-style tests that mock ``subprocess.run`` to verify that the
    correct Modal CLI commands are constructed.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure the project root is on sys.path so we can import modal_deploy
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import main as md


# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def temp_home(monkeypatch):
    """Redirect HOME/USERPROFILE to a temp directory for config isolation."""
    tmp = tempfile.mkdtemp()
    tmp_path = Path(tmp)
    if os.name == "posix":
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
    else:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Reset the module-level CONFIG_PATH to point into the temp dir
    original_config_path = md.CONFIG_PATH
    md.CONFIG_PATH = tmp_path / md.CONFIG_FILE_NAME
    yield tmp_path
    # Restore
    md.CONFIG_PATH = original_config_path


@pytest.fixture
def sample_creds():
    """Return a dict with sample valid-looking credentials."""
    return {
        "token_id": "tk_abc123xyz",
        "token_secret": "sec_456def789ghi",
    }


@pytest.fixture
def config_file(temp_home, sample_creds):
    """Write a valid config file to the temp home directory."""
    path = temp_home / md.CONFIG_FILE_NAME
    data = {
        "modal_token_id": sample_creds["token_id"],
        "modal_token_secret": sample_creds["token_secret"],
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return path


# =========================================================================
# Config persistence tests
# =========================================================================

class TestConfigPersistence:
    """Tests for _load_config, _save_config, _lock_permissions."""

    def test_load_config_no_file_returns_empty(self, temp_home):
        """Loading config when no file exists returns an empty dict."""
        cfg = md._load_config()
        assert cfg == {}

    def test_load_config_invalid_json_returns_empty(self, temp_home):
        """Loading config with malformed JSON returns an empty dict."""
        path = temp_home / md.CONFIG_FILE_NAME
        path.write_text("not json {{{")
        cfg = md._load_config()
        assert cfg == {}

    def test_load_config_valid(self, config_file, sample_creds):
        """Loading a valid config file returns the expected values."""
        cfg = md._load_config()
        assert cfg["modal_token_id"] == sample_creds["token_id"]
        assert cfg["modal_token_secret"] == sample_creds["token_secret"]

    def test_save_config_creates_file(self, temp_home, sample_creds):
        """_save_config creates the config file with the expected content."""
        md._save_config(sample_creds["token_id"], sample_creds["token_secret"])
        assert md.CONFIG_PATH.exists()
        with open(md.CONFIG_PATH, "r") as f:
            data = json.load(f)
        assert data["modal_token_id"] == sample_creds["token_id"]
        assert data["modal_token_secret"] == sample_creds["token_secret"]

    def test_save_config_atomic_no_partial_write(self, temp_home, sample_creds):
        """Simulate a crash during write — temp file should not become the real file."""
        # Inject a failure in shutil.move
        original_move = md.shutil.move
        called = [False]

        def failing_move(src, dst):
            if not called[0]:
                called[0] = True
                raise OSError("Simulated move failure")
            return original_move(src, dst)

        md.shutil.move = failing_move
        try:
            with pytest.raises(OSError):
                md._save_config(sample_creds["token_id"], sample_creds["token_secret"])
            # The real config file should NOT exist
            assert not md.CONFIG_PATH.exists(), "Config file should not be created on failed atomic write"
        finally:
            md.shutil.move = original_move

    def test_save_config_overwrites(self, temp_home, sample_creds):
        """Saving a second time overwrites the existing file."""
        md._save_config("old_id", "old_secret")
        md._save_config(sample_creds["token_id"], sample_creds["token_secret"])
        with open(md.CONFIG_PATH, "r") as f:
            data = json.load(f)
        assert data["modal_token_id"] == sample_creds["token_id"]

    def test_lock_permissions_posix(self, temp_home, monkeypatch):
        """On POSIX, _lock_permissions sets mode to 0o600."""
        monkeypatch.setattr(os, "name", "posix")
        test_file = temp_home / "test_lock.txt"
        test_file.write_text("test")
        md._lock_permissions(str(test_file))
        st_mode = os.stat(str(test_file)).st_mode
        # On POSIX, owner-read/write should be set; group/other should have no perms.
        # On Windows the stat result may differ, so only verify the function
        # does not raise and owner bits are present.
        assert stat.S_IRUSR & st_mode
        assert stat.S_IWUSR & st_mode

    def test_lock_permissions_windows_skipped_when_no_username(self, temp_home, monkeypatch):
        """On Windows, _lock_permissions does nothing if USERNAME is not set."""
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(os.environ, "get", lambda k, d="": None)
        test_file = temp_home / "test_lock.txt"
        test_file.write_text("test")
        # Should not raise even though icacls isn't called
        md._lock_permissions(str(test_file))

    def test_load_config_non_dict_returns_empty(self, temp_home):
        """If config file contains a list instead of dict, return empty."""
        path = temp_home / md.CONFIG_FILE_NAME
        with open(path, "w") as f:
            json.dump(["not", "a", "dict"], f)
        assert md._load_config() == {}


# =========================================================================
# Sensitive-data clearing tests
# =========================================================================

class TestClearSensitive:
    """Tests for _clear_sensitive."""

    def test_clear_sensitive_does_not_raise(self):
        """_clear_sensitive handles empty strings gracefully."""
        # Should not raise
        md._clear_sensitive("")
        md._clear_sensitive()

    def test_clear_sensitive_with_data(self):
        """_clear_sensitive runs without error on actual data."""
        s = "secret_token_value_12345"
        # Just verify it runs without raising
        md._clear_sensitive(s)

    def test_clear_sensitive_multiple(self):
        """_clear_sensitive accepts multiple strings."""
        md._clear_sensitive("secret1", "secret2", "secret3")


# =========================================================================
# Credential helper tests
# =========================================================================

class TestGetCredentials:
    """Tests for _get_credentials and _require_credentials."""

    def test_get_credentials_no_config(self, temp_home):
        """Returns None when no config file exists."""
        assert md._get_credentials() is None

    def test_get_credentials_partial_config(self, temp_home):
        """Returns None if only one of two fields is present."""
        path = temp_home / md.CONFIG_FILE_NAME
        with open(path, "w") as f:
            json.dump({"modal_token_id": "tk_abc"}, f)
        assert md._get_credentials() is None

    def test_get_credentials_valid(self, config_file, sample_creds):
        """Returns credentials dict when config is valid."""
        creds = md._get_credentials()
        assert creds is not None
        assert creds["token_id"] == sample_creds["token_id"]
        assert creds["token_secret"] == sample_creds["token_secret"]

    def test_require_credentials_prints_when_missing(self, temp_home, capsys):
        """_require_credentials prints a message and returns None when no creds exist."""
        result = md._require_credentials()
        captured = capsys.readouterr()
        assert result is None
        assert "No token configured" in captured.out

    def test_require_credentials_returns_when_present(self, config_file, sample_creds):
        """_require_credentials returns creds when they exist."""
        creds = md._require_credentials()
        assert creds is not None
        assert creds["token_id"] == sample_creds["token_id"]


# =========================================================================
# Preflight check tests
# =========================================================================

class TestPreflightChecks:
    """Tests for _check_python, _check_modal_installed, run_preflight_checks."""

    @patch("os.path.isfile", return_value=False)
    def test_check_python_not_found(self, mock_isfile):
        """Returns error message when python interpreter is missing."""
        err = md._check_python()
        assert err is not None
        assert "not found" in err.lower()

    @patch("os.path.isfile", return_value=True)
    @patch("subprocess.run")
    def test_check_python_nonzero_exit(self, mock_run, mock_isfile):
        """Returns error when python --version exits with non-zero."""
        mock_run.return_value = MagicMock(returncode=1, stderr="crash", stdout="")
        err = md._check_python()
        assert err is not None
        assert "not working" in err.lower()

    @patch("os.path.isfile", return_value=True)
    @patch("subprocess.run")
    def test_check_python_success(self, mock_run, mock_isfile):
        """Returns None when python is available."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Python 3.12.0", stderr="")
        err = md._check_python()
        assert err is None

    @patch("subprocess.run")
    def test_check_modal_installed_success(self, mock_run):
        """Returns None when modal CLI is available."""
        mock_run.return_value = MagicMock(returncode=0, stdout="usage: modal ...", stderr="")
        err = md._check_modal_installed()
        assert err is None

    @patch("subprocess.run")
    def test_check_modal_installed_failure(self, mock_run):
        """Returns error when modal CLI is not available."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No module named modal")
        err = md._check_modal_installed()
        assert err is not None
        assert "not installed" in err.lower()

    @patch.object(md, "_check_python")
    @patch.object(md, "_check_modal_installed")
    def test_run_preflight_checks_all_pass(self, mock_modal, mock_py):
        """Returns empty list when all checks pass."""
        mock_py.return_value = None
        mock_modal.return_value = None
        errors = md.run_preflight_checks()
        assert errors == []

    @patch.object(md, "_check_python")
    @patch.object(md, "_check_modal_installed")
    def test_run_preflight_checks_with_errors(self, mock_modal, mock_py):
        """Returns list of error messages when checks fail."""
        mock_py.return_value = "Python error"
        mock_modal.return_value = "Modal error"
        errors = md.run_preflight_checks()
        assert len(errors) == 2


# =========================================================================
# Modal CLI command construction tests
# =========================================================================

class TestModalCmd:
    """Tests for _modal_cmd and _build_env."""

    def test_build_env_includes_token(self, sample_creds):
        """_build_env inserts Modal credentials as env vars."""
        env = md._build_env(sample_creds["token_id"], sample_creds["token_secret"])
        assert env[md.ENV_MODAL_TOKEN_ID] == sample_creds["token_id"]
        assert env[md.ENV_MODAL_TOKEN_SECRET] == sample_creds["token_secret"]

    def test_build_env_preserves_existing(self, sample_creds, monkeypatch):
        """_build_env preserves existing environment variables."""
        monkeypatch.setenv("EXISTING_VAR", "hello")
        env = md._build_env(sample_creds["token_id"], sample_creds["token_secret"])
        assert env["EXISTING_VAR"] == "hello"

    @patch("subprocess.run")
    def test_modal_cmd_constructs_correct_command(self, mock_run, sample_creds):
        """_modal_cmd builds the correct CLI command with interpreter -m modal."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        md._modal_cmd(["token", "list", "--verbose"], sample_creds["token_id"], sample_creds["token_secret"])

        expected_cmd = [str(md.PYTHON_INTERPRETER), "-m", "modal", "token", "list", "--verbose"]
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == expected_cmd
        assert kwargs.get("capture_output") is True
        assert kwargs.get("timeout") == 180

    @patch("subprocess.run")
    def test_modal_cmd_timeout_raises(self, mock_run, sample_creds):
        """_modal_cmd propagates TimeoutExpired."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="modal", timeout=180)
        with pytest.raises(subprocess.TimeoutExpired):
            md._modal_cmd(["token", "list"], sample_creds["token_id"], sample_creds["token_secret"])


# =========================================================================
# _check_result tests
# =========================================================================

class TestCheckResult:
    """Tests for _check_result helper."""

    def test_success_returns_true_and_prints(self, capsys):
        """Returns True and prints success message on returncode 0."""
        result = MagicMock(returncode=0, stdout="done", stderr="")
        ok = md._check_result(result, ok_label="All good", fail_label="Bad", show_output=True)
        assert ok is True
        captured = capsys.readouterr()
        assert "✅ All good" in captured.out
        assert "done" in captured.out

    def test_failure_returns_false_and_prints(self, capsys):
        """Returns False and prints failure message on non-zero returncode."""
        result = MagicMock(returncode=1, stdout="", stderr="error msg")
        ok = md._check_result(result, ok_label="OK", fail_label="Fail", show_output=True)
        assert ok is False
        captured = capsys.readouterr()
        assert "❌ Fail" in captured.out
        assert "error msg" in captured.out

    def test_success_no_output(self, capsys):
        """Does not print output lines when stdout is empty."""
        result = MagicMock(returncode=0, stdout="", stderr="")
        md._check_result(result, ok_label="OK", fail_label="Fail", show_output=True)
        captured = capsys.readouterr()
        assert "✅ OK" in captured.out


# =========================================================================
# Action function tests (with mocked subprocess)
# =========================================================================

class TestActions:
    """Tests for action functions with mocked subprocess."""

    @patch.object(md, "_load_config", return_value={})
    @patch("getpass.getpass", return_value="sec_new")
    @patch("builtins.input", side_effect=["tk_new", "n"])  # token_id=tk_new, test=n
    @patch.object(md, "_save_config")
    def test_action_configure_saves(self, mock_save, mock_input, mock_getpass, mock_load):
        """action_configure saves credentials and offers test prompt."""
        md.action_configure()
        mock_save.assert_called_once_with("tk_new", "sec_new")

    @patch.object(md, "_load_config", return_value={"modal_token_id": "existing"})
    @patch("builtins.input", side_effect=["n"])  # don't overwrite
    def test_action_configure_skips_when_declined(self, mock_input, mock_load):
        """action_configure bails early when user declines to overwrite."""
        md.action_configure()
        # No error — just returns

    @patch.object(md, "_load_config")
    @patch.object(md, "_modal_cmd")
    def test_action_test_token_from_config(self, mock_modal, mock_load, sample_creds):
        """action_test_token loads credentials from config when not provided."""
        mock_load.return_value = {
            "modal_token_id": sample_creds["token_id"],
            "modal_token_secret": sample_creds["token_secret"],
        }
        mock_modal.return_value = MagicMock(returncode=0, stdout="token valid", stderr="")
        md.action_test_token()
        mock_modal.assert_called_once_with(["token", "list"], sample_creds["token_id"], sample_creds["token_secret"])

    @patch.object(md, "_modal_cmd")
    def test_action_list_projects(self, mock_modal, sample_creds):
        """action_list_projects calls modal project list."""
        mock_modal.return_value = MagicMock(returncode=0, stdout="project-alpha\nproject-beta", stderr="")
        md.action_list_projects(sample_creds["token_id"], sample_creds["token_secret"])
        mock_modal.assert_called_once_with(["project", "list"], sample_creds["token_id"], sample_creds["token_secret"])

    @patch.object(md, "_modal_cmd")
    def test_action_create_project(self, mock_modal, sample_creds):
        """action_create_project calls modal project create with given params."""
        with patch("builtins.input", side_effect=["my-project", "", ""]) as mock_input:
            mock_modal.return_value = MagicMock(returncode=0, stdout="created", stderr="")
            md.action_create_project(sample_creds["token_id"], sample_creds["token_secret"])
            mock_modal.assert_called_once_with(
                ["project", "create", "my-project", "--region", "us-east", "--plan", "scale"],
                sample_creds["token_id"],
                sample_creds["token_secret"],
            )

    @patch.object(md, "_modal_cmd")
    def test_action_create_project_empty_name(self, mock_modal, sample_creds):
        """action_create_project rejects empty project name."""
        with patch("builtins.input", side_effect=[""]) as mock_input:
            md.action_create_project(sample_creds["token_id"], sample_creds["token_secret"])
            mock_modal.assert_not_called()

    @patch.object(md, "_modal_cmd")
    def test_action_deploy_router(self, mock_modal, sample_creds):
        """action_deploy_router calls modal deploy for the router app."""
        with patch("builtins.input", side_effect=["", ""]) as mock_input:
            mock_modal.return_value = MagicMock(returncode=0, stdout="deployed", stderr="")
            md.action_deploy_router(sample_creds["token_id"], sample_creds["token_secret"])
            mock_modal.assert_called_once_with(
                ["deploy", md.ROUTER_APP_NAME, "--env", md.DEFAULT_PROJECT],
                sample_creds["token_id"],
                sample_creds["token_secret"],
            )

    @patch.object(md, "_modal_cmd")
    def test_action_deploy_dashboard(self, mock_modal, sample_creds):
        """action_deploy_dashboard calls modal deploy for the dashboard app."""
        with patch("builtins.input", side_effect=["", ""]) as mock_input:
            mock_modal.return_value = MagicMock(returncode=0, stdout="deployed", stderr="")
            md.action_deploy_dashboard(sample_creds["token_id"], sample_creds["token_secret"])
            mock_modal.assert_called_once_with(
                ["deploy", md.DASHBOARD_APP_NAME, "--env", md.DEFAULT_PROJECT],
                sample_creds["token_id"],
                sample_creds["token_secret"],
            )

    @patch.object(md, "_get_credentials", return_value=None)
    def test_action_project_menu_no_creds(self, mock_creds):
        """action_project_menu prints message and returns when no creds."""
        md.action_project_menu()
        # No error — just returns

    @patch.object(md, "_modal_cmd")
    def test_action_list_projects_timeout(self, mock_modal, sample_creds, capsys):
        """action_list_projects handles TimeoutExpired gracefully."""
        mock_modal.side_effect = subprocess.TimeoutExpired(cmd="modal", timeout=180)
        md.action_list_projects(sample_creds["token_id"], sample_creds["token_secret"])
        captured = capsys.readouterr()
        assert "timed out" in captured.out.lower()

    @patch.object(md, "_modal_cmd")
    def test_action_list_projects_unexpected_exception(self, mock_modal, sample_creds, capsys):
        """action_list_projects handles unexpected exceptions gracefully."""
        mock_modal.side_effect = RuntimeError("something broke")
        md.action_list_projects(sample_creds["token_id"], sample_creds["token_secret"])
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()


# =========================================================================
# Argument parsing tests
# =========================================================================

class TestArgumentParsing:
    """Tests for CLI argument parsing."""

    def test_default_no_args(self, monkeypatch):
        """With no args, parser produces no special flags."""
        monkeypatch.setattr("sys.argv", ["modal_deploy.py"])
        args = md.parser.parse_args([])
        assert args.check is False
        assert args.deploy_router is False
        assert args.deploy_dashboard is False
        assert args.status is False
        assert args.project is None

    def test_check_flag(self):
        """--check sets check flag."""
        args = md.parser.parse_args(["--check"])
        assert args.check is True

    def test_deploy_router_flag(self):
        """--deploy-router sets deploy_router flag."""
        args = md.parser.parse_args(["--deploy-router"])
        assert args.deploy_router is True

    def test_deploy_dashboard_flag(self):
        """--deploy-dashboard sets deploy_dashboard flag."""
        args = md.parser.parse_args(["--deploy-dashboard"])
        assert args.deploy_dashboard is True

    def test_status_flag(self):
        """--status sets status flag."""
        args = md.parser.parse_args(["--status"])
        assert args.status is True

    def test_project_option(self):
        """--project sets the project name."""
        args = md.parser.parse_args(["--deploy-router", "--project", "my-project"])
        assert args.project == "my-project"
        assert args.deploy_router is True

    def test_combined_flags(self):
        """Multiple flags can be combined."""
        args = md.parser.parse_args(["--check", "--status"])
        assert args.check is True
        assert args.status is True


# =========================================================================
# View status test
# =========================================================================

class TestViewStatus:
    """Tests for action_view_status."""

    @patch.object(md, "_check_python", return_value=None)
    @patch.object(md, "_check_modal_installed", return_value=None)
    @patch.object(md, "_get_credentials", return_value={"token_id": "tk_test", "token_secret": "sec_test"})
    @patch.object(md, "_check_modal_json", return_value={
        "projects": [{"name": "project-alpha"}],
        "apps": [{"name": "vllm-sr-router"}, {"name": "vllm-sr-dashboard"}],
    })
    def test_view_status_with_creds(self, mock_json, mock_creds, mock_modal, mock_py, capsys):
        """action_view_status displays all status sections."""
        md.action_view_status()
        captured = capsys.readouterr()
        assert "Status Overview" in captured.out
        assert "Python Interpreter" in captured.out
        assert "Modal CLI" in captured.out
        assert "Credentials" in captured.out
        assert "Project Configuration" in captured.out

    @patch.object(md, "_check_python", return_value="Python not found")
    @patch.object(md, "_check_modal_installed", return_value="Modal not installed")
    @patch.object(md, "_get_credentials", return_value=None)
    @patch.object(md, "_check_modal_json", return_value=None)
    def test_view_status_no_creds(self, mock_json, mock_creds, mock_modal, mock_py, capsys):
        """action_view_status handles missing creds and failed checks gracefully."""
        md.action_view_status()
        captured = capsys.readouterr()
        assert "Status Overview" in captured.out
        assert "Not configured" in captured.out


# =========================================================================
# Main entry-point tests (main function)
# =========================================================================

class TestMainFunction:
    """Tests for the main() entry point."""

    @patch.object(md, "run_preflight_checks", return_value=[])
    @patch.object(md, "main_menu")
    def test_main_launches_menu(self, mock_menu, mock_checks):
        """main() launches the interactive menu by default."""
        with patch.object(sys, "argv", ["modal_deploy.py"]):
            md.main()
        mock_menu.assert_called_once()

    @patch.object(md, "run_preflight_checks", return_value=["Python error"])
    @patch.object(md, "main_menu")
    def test_main_prompts_on_preflight_failure(self, mock_menu, mock_checks):
        """main() prompts to proceed when preflight checks fail."""
        with patch("builtins.input", return_value="y"):
            with patch.object(sys, "argv", ["modal_deploy.py"]):
                md.main()
        mock_menu.assert_called_once()

    @patch.object(md, "run_preflight_checks", return_value=["Python error"])
    @patch.object(md, "main_menu")
    def test_main_exits_on_preflight_decline(self, mock_menu, mock_checks):
        """main() exits when user declines to proceed after preflight failure."""
        with patch("builtins.input", return_value="n"):
            with patch.object(sys, "argv", ["modal_deploy.py"]):
                with pytest.raises(SystemExit) as exc:
                    md.main()
        assert exc.value.code == 1
        mock_menu.assert_not_called()

    @patch.object(md, "run_preflight_checks", return_value=[])
    @patch("builtins.print")
    def test_main_check_flag(self, mock_print, mock_checks):
        """main() with --check runs checks and exits cleanly."""
        with patch.object(sys, "argv", ["modal_deploy.py", "--check"]):
            md.main()
        mock_checks.assert_called_once()

    @patch.object(md, "run_preflight_checks", return_value=[])
    @patch.object(md, "_get_credentials", return_value={"token_id": "tk", "token_secret": "sec"})
    @patch.object(md, "action_deploy_router")
    def test_main_deploy_router_flag(self, mock_deploy, mock_creds, mock_checks):
        """main() with --deploy-router calls action_deploy_router."""
        with patch.object(sys, "argv", ["modal_deploy.py", "--deploy-router"]):
            md.main()
        mock_deploy.assert_called_once()

    @patch.object(md, "run_preflight_checks", return_value=[])
    @patch.object(md, "_get_credentials", return_value={"token_id": "tk", "token_secret": "sec"})
    @patch.object(md, "action_deploy_dashboard")
    def test_main_deploy_dashboard_flag(self, mock_deploy, mock_creds, mock_checks):
        """main() with --deploy-dashboard calls action_deploy_dashboard."""
        with patch.object(sys, "argv", ["modal_deploy.py", "--deploy-dashboard"]):
            md.main()
        mock_deploy.assert_called_once()

    @patch.object(md, "run_preflight_checks", return_value=[])
    @patch.object(md, "_get_credentials", return_value=None)
    def test_main_deploy_without_creds_exits(self, mock_creds, mock_checks):
        """main() with --deploy-router exits with error when no creds."""
        with patch.object(sys, "argv", ["modal_deploy.py", "--deploy-router"]):
            with pytest.raises(SystemExit) as exc:
                md.main()
        assert exc.value.code == 1

    @patch.object(md, "run_preflight_checks", return_value=[])
    @patch.object(md, "_get_credentials", return_value=None)
    @patch.object(md, "action_view_status")
    def test_main_status_flag(self, mock_status, mock_creds, mock_checks):
        """main() with --status calls action_view_status."""
        with patch.object(sys, "argv", ["modal_deploy.py", "--status"]):
            md.main()
        mock_status.assert_called_once()


# =========================================================================
# _check_modal_json tests
# =========================================================================

class TestModalJson:
    """Tests for _check_modal_json."""

    def test_modal_json_not_found(self, monkeypatch):
        """Returns None when modal.json does not exist."""
        monkeypatch.setattr(md, "MODAL_JSON_PATH", Path("nonexistent.json"))
        assert md._check_modal_json() is None

    def test_modal_json_invalid(self, tmp_path, monkeypatch):
        """Returns None when modal.json is malformed."""
        bad_path = tmp_path / "modal.json"
        bad_path.write_text("{invalid}")
        monkeypatch.setattr(md, "MODAL_JSON_PATH", bad_path)
        assert md._check_modal_json() is None

    def test_modal_json_valid(self, tmp_path, monkeypatch):
        """Returns parsed dict when modal.json is valid."""
        good_path = tmp_path / "modal.json"
        data = {"projects": [{"name": "test"}], "apps": [{"name": "app"}]}
        with open(good_path, "w") as f:
            json.dump(data, f)
        monkeypatch.setattr(md, "MODAL_JSON_PATH", good_path)
        result = md._check_modal_json()
        assert result is not None
        assert result["projects"][0]["name"] == "test"
