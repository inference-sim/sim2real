"""Shared logging functions for pipeline scripts."""
import sys
import time


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def info(msg: str) -> None: print(f"{_ts()} {_c('34', '[INFO]  ')}{msg}")
def ok(msg: str)   -> None: print(f"{_ts()} {_c('32', '[OK]    ')}{msg}")
def warn(msg: str) -> None: print(f"{_ts()} {_c('33', '[WARN]  ')}{msg}")
def err(msg: str)  -> None: print(f"{_ts()} {_c('31', '[ERROR] ')}{msg}", file=sys.stderr)
