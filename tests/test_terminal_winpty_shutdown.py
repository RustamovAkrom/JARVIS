"""Regression tests for Windows PTY shutdown and PID handling."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from gui.app.main_window import MainWindow, _WinPTYTerminal


class _FakeWinPtyProcess:
    def __init__(self, pid=4321):
        self.fileobj = Mock()
        self.pid = pid
        self.alive = True
        self.closed = False

    def isalive(self):
        return self.alive

    def read(self, _size):
        return ""

    def write(self, _data):
        pass

    def setwinsize(self, _rows, _columns):
        pass

    def close(self, force=False):
        self.closed = True
        if force:
            self.alive = False

    def terminate(self, force=False):
        if force:
            self.alive = False


def _owner_for(pty):
    session = SimpleNamespace(pty=pty, closing=True)
    return SimpleNamespace(
        _terminal_sessions={"terminal-1": session},
        _append_activity=Mock(),
        _remove_terminal_session=Mock(),
    ), session


def test_winpty_adapter_exposes_the_underlying_child_pid():
    process = _FakeWinPtyProcess(pid=4321)
    terminal = _WinPTYTerminal(process)

    assert terminal.pid == 4321


def test_force_close_winpty_uses_taskkill_and_disposes_adapter():
    process = _FakeWinPtyProcess(pid=4321)
    terminal = _WinPTYTerminal(process)
    owner, session = _owner_for(terminal)

    def taskkill(*_args, **_kwargs):
        process.alive = False
        return SimpleNamespace(stderr="")

    with (
        patch("gui.app.main_window.os.name", "nt"),
        patch("gui.app.main_window.subprocess.run", side_effect=taskkill) as run,
    ):
        MainWindow._force_close_terminal_session(owner, "terminal-1")

    assert run.call_args.args[0] == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert process.closed
    assert session.pty is None
    owner._remove_terminal_session.assert_called_once_with("terminal-1")


def test_force_close_winpty_falls_back_to_adapter_kill_without_pid():
    process = _FakeWinPtyProcess()
    del process.pid
    terminal = _WinPTYTerminal(process)
    owner, session = _owner_for(terminal)

    with patch("gui.app.main_window.os.name", "nt"), patch(
        "gui.app.main_window.subprocess.run"
    ) as run:
        MainWindow._force_close_terminal_session(owner, "terminal-1")

    run.assert_not_called()
    assert not process.alive
    assert process.closed
    assert session.pty is None
    owner._remove_terminal_session.assert_called_once_with("terminal-1")
