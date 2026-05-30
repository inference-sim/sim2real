"""Shared logging functions for pipeline scripts."""
import sys
import time


def _c(code: str, text: str, *, stream=None) -> str:
    target = stream or sys.stdout
    return f"\033[{code}m{text}\033[0m" if target.isatty() else text


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def info(msg: str) -> None: print(f"{_ts()} {_c('34', '[INFO]  ')}{msg}")
def ok(msg: str)   -> None: print(f"{_ts()} {_c('32', '[OK]    ')}{msg}")
def warn(msg: str) -> None: print(f"{_ts()} {_c('33', '[WARN]  ')}{msg}")
def err(msg: str)  -> None: print(f"{_ts()} {_c('31', '[ERROR] ', stream=sys.stderr)}{msg}", file=sys.stderr)
