"""Tests for file manager recursive search helpers."""

from pathlib import PurePosixPath

from ashyterm.filemanager import search as search_module
from ashyterm.filemanager.search import FileSearchMixin


class _FakeSearch(FileSearchMixin):
    _recursive_search_generation = 1


class _FakeProcess:
    def __init__(self):
        self.stdout = iter(())
        self.stderr = iter(())
        self.returncode = 0
        self.killed = False

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class _FakeErrorProcess(_FakeProcess):
    def __init__(self):
        super().__init__()
        self.stderr = iter(("find: /root: Permission denied\n",))
        self.returncode = 1


def test_local_recursive_search_captures_process_error(monkeypatch):
    popen_kwargs = {}

    def fake_popen(_command, **kwargs):
        popen_kwargs.update(kwargs)
        return _FakeErrorProcess()

    monkeypatch.setattr(search_module.subprocess, "Popen", fake_popen)

    results, error_message, truncated = _FakeSearch()._search_local(
        1, ["find"], PurePosixPath("/tmp"), use_fd=False
    )

    assert results == []
    assert error_message == "find: /root: Permission denied"
    assert truncated is False
    assert popen_kwargs["stderr"] is search_module.subprocess.PIPE


def test_local_recursive_search_omits_error_on_success(monkeypatch):
    def fake_popen(_command, **_kwargs):
        return _FakeProcess()

    monkeypatch.setattr(search_module.subprocess, "Popen", fake_popen)

    results, error_message, truncated = _FakeSearch()._search_local(
        1, ["find"], PurePosixPath("/tmp"), use_fd=False
    )

    assert results == []
    assert error_message == ""
    assert truncated is False


def test_fd_search_uses_xargs_no_run_if_empty():
    fm = _FakeSearch()
    fm._fd_command_name = "fd"

    command = fm._build_fd_command("/tmp", "missing", show_hidden=False)

    assert command[:2] == ["sh", "-c"]
    assert "xargs -r -0 ls" in command[2]
