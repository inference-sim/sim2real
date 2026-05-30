"""Tests for pipeline/lib/log.py."""
import re
import sys

from pipeline.lib.log import info, ok, warn, err


_TS_PATTERN = re.compile(r"^\d{2}:\d{2}:\d{2} ")


def test_info_writes_to_stdout(capsys):
    info("hello")
    out = capsys.readouterr()
    assert "hello" in out.out
    assert out.err == ""


def test_ok_writes_to_stdout(capsys):
    ok("done")
    out = capsys.readouterr()
    assert "done" in out.out
    assert out.err == ""


def test_warn_writes_to_stdout(capsys):
    warn("careful")
    out = capsys.readouterr()
    assert "careful" in out.out
    assert out.err == ""


def test_err_writes_to_stderr(capsys):
    err("broken")
    out = capsys.readouterr()
    assert "broken" in out.err
    assert "broken" not in out.out


def test_timestamp_prefix(capsys):
    info("test msg")
    out = capsys.readouterr().out
    assert _TS_PATTERN.match(out)


def test_no_ansi_when_not_tty(capsys, monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    info("plain")
    out = capsys.readouterr().out
    assert "\033[" not in out
    assert "plain" in out
