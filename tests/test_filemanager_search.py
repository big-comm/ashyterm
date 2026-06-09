"""Tests for file manager recursive search helpers."""

from pathlib import PurePosixPath

from ashyterm.filemanager import search as search_module
from ashyterm.filemanager.search import FileSearchMixin


class _FakeSearch(FileSearchMixin):
    _recursive_search_generation = 1


class _FakeProcess:
    stdout = iter(())
    stderr = None
    returncode = 0

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


def test_local_recursive_search_discards_stderr_to_avoid_pipe_deadlock(monkeypatch):
    popen_kwargs = {}

    def fake_popen(_command, **kwargs):
        popen_kwargs.update(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(search_module.subprocess, "Popen", fake_popen)

    results, error_message, truncated = _FakeSearch()._search_local(
        1, ["find"], PurePosixPath("/tmp"), use_fd=False
    )

    assert results == []
    assert error_message == ""
    assert truncated is False
    assert popen_kwargs["stderr"] is search_module.subprocess.DEVNULL


def test_fd_search_uses_xargs_no_run_if_empty():
    fm = _FakeSearch()
    fm._fd_command_name = "fd"

    command = fm._build_fd_command("/tmp", "missing", show_hidden=False)

    assert command[:2] == ["sh", "-c"]
    assert "xargs -r -0 ls" in command[2]
