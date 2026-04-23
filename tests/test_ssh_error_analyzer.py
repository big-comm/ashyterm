"""Tests for SSH error analyzer — pure logic, no GTK/VTE required."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestDecodeExitCode:
    """Tests for decode_exit_code().

    child_status is the raw waitpid() status, not the exit code itself.
    Exit code 0 → status 0; exit code 1 → status 256 (1 << 8).
    """

    def test_normal_exit_zero(self):
        from ashyterm.terminal.ssh_error_analyzer import decode_exit_code

        assert decode_exit_code(0) == 0

    def test_normal_exit_code_1(self):
        from ashyterm.terminal.ssh_error_analyzer import decode_exit_code

        # exit(1) → status = 1 << 8 = 256
        assert decode_exit_code(256) == 1

    def test_normal_exit_code_255(self):
        from ashyterm.terminal.ssh_error_analyzer import decode_exit_code

        # exit(255) → status = 255 << 8 = 65280
        assert decode_exit_code(65280) == 255

    def test_signal_exit_sigint(self):
        from ashyterm.terminal.ssh_error_analyzer import decode_exit_code

        # Signal 2 (SIGINT) → 128 + 2 = 130
        assert decode_exit_code(2) == 130


class TestAnalyzeExitStatus:
    """Tests for analyze_exit_status().

    child_status is raw waitpid() status.
    """

    def test_normal_ssh_exit(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_exit_status

        info = {"type": "ssh"}
        result = analyze_exit_status(info, 0)

        assert result["ssh_failed"] is False
        assert result["is_ssh"] is True
        assert result["is_user_terminated"] is False

    def test_ssh_non_zero_failure(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_exit_status

        info = {"type": "ssh"}
        # exit code 1 → status 256
        result = analyze_exit_status(info, 256)

        assert result["ssh_failed"] is True
        assert result["is_user_terminated"] is False

    def test_user_terminated_sigint(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_exit_status

        info = {"type": "ssh"}
        # SIGINT signal → status 2 → decoded as 130
        result = analyze_exit_status(info, 2)

        assert result["ssh_failed"] is False  # User terminated → not a failure
        assert result["is_user_terminated"] is True

    def test_user_terminated_codes(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_exit_status

        info = {"type": "ssh"}
        # 130 = 128+2 (SIGINT), 137 = 128+9 (SIGKILL), 143 = 128+15 (SIGTERM)
        for signal_num in [2, 9, 15]:
            result = analyze_exit_status(info, signal_num)
            assert result["is_user_terminated"] is True, f"Signal {signal_num}"

    def test_closed_by_user_flag(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_exit_status

        info = {"type": "ssh", "_closed_by_user": True}
        result = analyze_exit_status(info, 256)

        assert result["ssh_failed"] is False
        assert result["closed_by_user"] is True

    def test_local_terminal_not_ssh(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_exit_status

        info = {"type": "local"}
        result = analyze_exit_status(info, 256)

        assert result["ssh_failed"] is False
        assert result["is_ssh"] is False

    def test_sftp_treated_as_ssh(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_exit_status

        info = {"type": "sftp"}
        result = analyze_exit_status(info, 256)

        assert result["ssh_failed"] is True
        assert result["is_ssh"] is True


class TestHasConnectionError:
    """Tests for has_connection_error()."""

    def test_no_error(self):
        from ashyterm.terminal.ssh_error_analyzer import has_connection_error

        assert has_connection_error("Welcome to Ubuntu 24.04 LTS") is False

    def test_connection_refused(self):
        from ashyterm.terminal.ssh_error_analyzer import has_connection_error

        assert has_connection_error("connection refused") is True

    def test_permission_denied(self):
        from ashyterm.terminal.ssh_error_analyzer import has_connection_error

        assert has_connection_error("permission denied") is True

    def test_host_key_verification_failed(self):
        from ashyterm.terminal.ssh_error_analyzer import has_connection_error

        assert has_connection_error("host key verification failed") is True

    def test_broken_pipe(self):
        from ashyterm.terminal.ssh_error_analyzer import has_connection_error

        assert has_connection_error("broken pipe") is True

    def test_no_route_to_host(self):
        from ashyterm.terminal.ssh_error_analyzer import has_connection_error

        assert has_connection_error("no route to host") is True

    def test_connection_timed_out(self):
        from ashyterm.terminal.ssh_error_analyzer import has_connection_error

        assert has_connection_error("connection timed out") is True

    def test_case_sensitive(self):
        from ashyterm.terminal.ssh_error_analyzer import has_connection_error

        # Patterns are lowercase; uppercase text should not match
        assert has_connection_error("CONNECTION REFUSED") is False


class TestHasShellPrompt:
    """Tests for has_shell_prompt()."""

    def test_bash_prompt(self):
        from ashyterm.terminal.ssh_error_analyzer import has_shell_prompt

        assert has_shell_prompt("user@host:~$ ") is True

    def test_root_prompt(self):
        from ashyterm.terminal.ssh_error_analyzer import has_shell_prompt

        assert has_shell_prompt("root@host:/# ") is True

    def test_welcome_message(self):
        from ashyterm.terminal.ssh_error_analyzer import has_shell_prompt

        assert has_shell_prompt("welcome to ubuntu") is True

    def test_last_login(self):
        from ashyterm.terminal.ssh_error_analyzer import has_shell_prompt

        assert has_shell_prompt("last login: mon apr  7 10:00:00 2026") is True

    def test_no_prompt(self):
        from ashyterm.terminal.ssh_error_analyzer import has_shell_prompt

        assert has_shell_prompt("Connection closed by remote host.") is False

    def test_empty_string(self):
        from ashyterm.terminal.ssh_error_analyzer import has_shell_prompt

        assert has_shell_prompt("") is False


class TestAnalyzeSshError:
    """Tests for analyze_ssh_error()."""

    def test_returns_dict_with_expected_keys(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_ssh_error

        result = analyze_ssh_error(255, "Connection refused")

        assert "error_type" in result
        assert "error_description" in result
        assert "is_auth_error" in result
        assert "is_host_key_error" in result

    def test_is_auth_error_false_for_connection_refused(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_ssh_error

        result = analyze_ssh_error(255, "Connection refused")
        assert result["is_auth_error"] is False

    def test_is_host_key_error(self):
        from ashyterm.terminal.ssh_error_analyzer import analyze_ssh_error

        result = analyze_ssh_error(255, "Host key verification failed")
        assert result["is_host_key_error"] is True
